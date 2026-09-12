import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from webmcp_resilience.agent_control import AgentPolicy, LocalMCPControlAdapter
from webmcp_resilience.cli import _emit, app
from webmcp_resilience.commands import CommandAPI, ToolContractDriftError, build_webmcp_compatibility_report
from webmcp_resilience.config import Config
from webmcp_resilience.console_client import bundle_comparison, load_bundle
from webmcp_resilience.engine.invariants import InvariantError
from webmcp_resilience.engine.runner import ScenarioRunner
from webmcp_resilience.models.bundle import (
    ApprovalRecord,
    Compatibility,
    CompatibilityRequirements,
    RunBundle,
)
from webmcp_resilience.models.scenario import Scenario
from webmcp_resilience.models.trace import TraceRun
from webmcp_resilience.tool_contracts import (
    build_inventory_contract,
    compare_tool_inventories,
    redact_tool_inventory,
    validate_contract_expectations,
)


def tool(
    name: str = "reserve_inventory",
    *,
    input_schema: dict | None = None,
    output_schema: dict | None = None,
    annotations: dict | None = None,
    description: str = "Reserve stock",
) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": input_schema or {"type": "object", "properties": {}},
        "outputSchema": output_schema,
        "annotations": annotations or {},
        "runtimeId": "volatile",
    }


def test_canonical_inventory_fingerprints_are_stable_sorted_and_redacted() -> None:
    first = [
        tool("z", input_schema={"properties": {"access_token": {"type": "string", "default": "token=secret-value"}}, "type": "object"}),
        tool("a", annotations={"vendorSecret": "secret-value", "readOnlyHint": True}),
    ]
    second = [
        {"annotations": {"readOnlyHint": True, "vendorSecret": "different-secret"}, "name": "a", "inputSchema": {"properties": {}, "type": "object"}},
        {"name": "z", "runtimeId": "different", "inputSchema": {"type": "object", "properties": {"access_token": {"default": "token=another-secret", "type": "string"}}}, "annotations": {}, "outputSchema": None},
    ]
    left = build_inventory_contract(first)
    right = build_inventory_contract(second)
    assert left["inventory_fingerprint"] == right["inventory_fingerprint"]
    assert [item["name"] for item in left["tools"]] == ["a", "z"]
    assert all(len(item["fingerprint"]) == 64 for item in left["tools"])
    rendered = json.dumps(left)
    assert "secret-value" not in rendered
    assert "access_token" in rendered
    assert "runtimeId" not in rendered


def test_descriptions_are_excluded_unless_explicitly_configured() -> None:
    before = build_inventory_contract([tool(description="before")])
    after = build_inventory_contract([tool(description="after")])
    assert before["inventory_fingerprint"] == after["inventory_fingerprint"]
    configured_before = build_inventory_contract([tool(description="before")], include_descriptions=True)
    configured_after = build_inventory_contract([tool(description="after")], include_descriptions=True)
    assert configured_before["inventory_fingerprint"] != configured_after["inventory_fingerprint"]


def test_bundle_persistence_preserves_secret_named_schema_structure_without_secret_values(tmp_path: Path) -> None:
    inventory = [tool(input_schema={
        "type": "object",
        "properties": {"access_token": {"type": "string", "default": "token=private-value"}},
    })]
    contract = build_inventory_contract(inventory)
    bundle = RunBundle(
        command="preflight",
        tool_inventory=inventory,
        inventory_contract=contract,
        compatibility=Compatibility(tool_inventory_fingerprint=contract["inventory_fingerprint"]),
    )
    persisted = json.loads(bundle.write(tmp_path / "bundle.json").read_text())
    assert persisted["tool_inventory"][0]["inputSchema"]["properties"]["access_token"]["type"] == "string"
    assert persisted["inventory_contract"]["tools"][0]["contract"]["input_schema"]["properties"]["access_token"]["type"] == "string"
    assert "private-value" not in json.dumps(persisted)
    reloaded = RunBundle.model_validate(persisted)
    assert build_inventory_contract(reloaded.tool_inventory)["inventory_fingerprint"] == contract["inventory_fingerprint"]


def test_opaque_values_beneath_sensitive_schema_properties_never_cross_evidence_surfaces(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    opaque_values = {
        "description": "abc123-description",
        "title": "abc123-title",
        "pattern": "abc123-pattern",
        "format": "abc123-format",
        "contentMediaType": "abc123-media-type",
        "x-private-extension": "abc123-extension",
        "unknownKeyword": "abc123-unknown",
        "nested": "abc123-nested",
    }
    secret_schema = {
        "type": "object",
        "properties": {
            "access_token": {
                "type": "object",
                "description": opaque_values["description"],
                "title": opaque_values["title"],
                "pattern": opaque_values["pattern"],
                "format": opaque_values["format"],
                "contentMediaType": opaque_values["contentMediaType"],
                "x-private-extension": {"value": opaque_values["x-private-extension"]},
                "unknownKeyword": [opaque_values["unknownKeyword"], {"value": opaque_values["nested"]}],
                "properties": {
                    "nested_value": {
                        "type": "string",
                        "description": opaque_values["nested"],
                    }
                },
                "required": ["nested_value"],
                "allOf": [{"type": "object", "x-note": opaque_values["nested"]}],
            }
        },
    }
    inventory = [tool(
        input_schema=json.dumps(secret_schema),  # type: ignore[arg-type]
        output_schema=json.dumps(secret_schema),  # type: ignore[arg-type]
    )]
    contract = build_inventory_contract(inventory)
    object_contract = build_inventory_contract([tool(
        input_schema=secret_schema,
        output_schema=secret_schema,
    )])
    assert contract["inventory_fingerprint"] == object_contract["inventory_fingerprint"]
    contract_text = json.dumps(contract)
    assert "access_token" in contract_text
    safe_secret = contract["tools"][0]["contract"]["input_schema"]["properties"]["access_token"]
    safe_output_secret = contract["tools"][0]["contract"]["output_schema"]["properties"]["access_token"]
    assert safe_secret["type"] == "object"
    assert safe_output_secret["type"] == "object"
    assert safe_secret["properties"]["nested_value"]["type"] == "string"
    assert safe_secret["required"] == ["nested_value"]
    assert safe_secret["allOf"][0]["type"] == "object"
    assert all(value not in contract_text for value in opaque_values.values())

    report = build_webmcp_compatibility_report({}, inventory, browser={})
    bundle = RunBundle(
        command="preflight",
        run_id="opaque-contract",
        preflight=report,
        tool_inventory=inventory,
        inventory_contract=contract,
        compatibility=Compatibility(tool_inventory_fingerprint=contract["inventory_fingerprint"]),
    )
    api = CommandAPI(Config(), output_dir=tmp_path / "runs")
    _emit(bundle, json_output=True, api=api)
    cli_payload = json.loads(capsys.readouterr().out)
    cli_text = json.dumps(cli_payload)
    assert all(value not in cli_text for value in opaque_values.values())
    assert cli_payload["inventory_contract"]["tools"][0]["contract"]["input_schema"]["properties"]["access_token"]["type"] == "object"
    assert cli_payload["preflight"]["inventory_contract"]["tools"][0]["contract"]["input_schema"]["properties"]["access_token"]["type"] == "object"

    persisted_path = api.bundle_path("opaque-contract")
    persisted_text = persisted_path.read_text()
    assert all(value not in persisted_text for value in opaque_values.values())
    console_bundle = load_bundle(persisted_path)
    assert console_bundle is not None
    assert console_bundle.tool_inventory[0]["inputSchema"]["properties"]["access_token"]["type"] == "object"

    replay_bundle = bundle.model_copy(update={"command": "replay", "run_id": "opaque-replay"})
    replay_text = replay_bundle.write(tmp_path / "replay-bundle.json").read_text()
    assert all(value not in replay_text for value in opaque_values.values())
    assert (
        json.loads(replay_text)["tool_inventory"][0]["inputSchema"]["properties"]
        ["access_token"]["type"]
        == "object"
    )

    project = tmp_path / "project"
    project.mkdir()
    mcp = LocalMCPControlAdapter(
        CommandAPI(Config(), output_dir=project / ".webmcp" / "runs"),
        AgentPolicy(project, "http://localhost:3000"),
    )
    response = mcp._result("preflight", bundle)
    response_text = json.dumps(response)
    assert all(value not in response_text for value in opaque_values.values())
    assert response["bundle"]["tool_inventory"][0]["inputSchema"]["properties"]["access_token"]["type"] == "object"


def test_invalid_json_schema_strings_are_replaced_without_persisting_raw_contents(
    tmp_path: Path,
) -> None:
    opaque_input = '{"properties":{"access_token":{"description":"abc123-input"}}'
    opaque_output = "abc123-output-invalid-json"
    inventory = [tool(
        input_schema=opaque_input,  # type: ignore[arg-type]
        output_schema=opaque_output,  # type: ignore[arg-type]
    )]
    contract = build_inventory_contract(inventory)
    safe_inventory = redact_tool_inventory(inventory)
    bundle = RunBundle(
        command="replay",
        run_id="invalid-json-schema",
        tool_inventory=inventory,
        inventory_contract=contract,
    )
    persisted = bundle.write(tmp_path / "invalid-json-schema.json").read_text()
    for rendered in (json.dumps(contract), json.dumps(safe_inventory), persisted):
        assert opaque_input not in rendered
        assert opaque_output not in rendered
        assert "abc123-input" not in rendered


@pytest.mark.parametrize(
    ("before", "after", "impact", "category"),
    [
        ([tool("gone")], [], "breaking", "removed_tools"),
        ([tool()], [tool(input_schema={"type": "object", "properties": {"quantity": {"type": "integer"}}, "required": ["quantity"]})], "breaking", "changed_input_schemas"),
        ([tool(input_schema={"type": "object", "properties": {"kind": {"type": "string", "enum": ["a", "b"]}}})], [tool(input_schema={"type": "object", "properties": {"kind": {"type": "string", "enum": ["a"]}}})], "breaking", "changed_input_schemas"),
        ([tool(input_schema={"type": "object", "properties": {"value": {"type": ["string", "null"]}}})], [tool(input_schema={"type": "object", "properties": {"value": {"type": "string"}}})], "breaking", "changed_input_schemas"),
        ([tool(annotations={"readOnlyHint": True})], [tool(annotations={"readOnlyHint": False})], "breaking", "changed_annotations"),
        ([tool(output_schema={"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]})], [tool(output_schema={"type": "object", "properties": {}})], "breaking", "changed_output_schemas"),
        ([tool(output_schema={"type": "string"})], [tool(output_schema={"type": "number"})], "breaking", "changed_output_schemas"),
        ([tool()], [tool(input_schema={"type": "object", "properties": {"note": {"type": "string"}}})], "compatible", "changed_input_schemas"),
        ([tool(description="before")], [tool(description="after")], "compatible", "descriptive_only_changes"),
        ([tool(input_schema={"type": "object", "minProperties": 1})], [tool(input_schema={"type": "object", "minProperties": 2})], "unknown", "changed_input_schemas"),
    ],
)
def test_contract_drift_classification(before: list[dict], after: list[dict], impact: str, category: str) -> None:
    report = compare_tool_inventories(before, after)
    assert report["changed"] is True
    assert report["policy_impact"] == impact
    assert report[category]


def test_inventory_diff_reports_added_removed_changed_and_unchanged_names() -> None:
    before = [tool("removed"), tool("changed"), tool("same")]
    after = [tool("added"), tool("changed", annotations={"destructiveHint": True}), tool("same")]
    report = compare_tool_inventories(before, after)
    assert report["added_tools"] == ["added"]
    assert report["removed_tools"] == ["removed"]
    assert report["changed_annotations"] == ["changed"]
    assert report["unchanged_tools"] == ["same"]


def test_description_comparison_fingerprints_follow_opt_in_mode() -> None:
    before = [tool(description="before")]
    after = [tool(description="after")]
    ignored = compare_tool_inventories(before, after, include_descriptions=False)
    included = compare_tool_inventories(before, after, include_descriptions=True)
    assert ignored["policy_impact"] == "compatible"
    assert ignored["descriptive_only_changes"] == ["reserve_inventory"]
    assert ignored["baseline_inventory_fingerprint"] == ignored["current_inventory_fingerprint"]
    assert included["policy_impact"] == "compatible"
    assert included["descriptive_only_changes"] == ["reserve_inventory"]
    assert included["baseline_inventory_fingerprint"] != included["current_inventory_fingerprint"]


def test_yaml_contract_expectations_are_optional_and_validate_live_discovery(tmp_path: Path) -> None:
    scenario = Scenario.model_validate({
        "name": "contracts",
        "actors": {"agent": [{"invoke": "reserve_inventory", "args": {"sku": "A", "quantity": 1}}]},
        "tool_contracts": {
            "reserve_inventory": {
                "required_inputs": ["sku", "quantity"],
                "read_only": False,
                "semantic_version": "1",
                "result_invariants": ["results.reserve_inventory.OK == 1"],
                "expected_result_codes": ["OK", "STALE_STATE"],
            }
        },
    })
    inventory = [tool(input_schema={
        "type": "object",
        "properties": {"sku": {"type": "string"}, "quantity": {"type": "integer"}},
        "required": ["sku", "quantity"],
    }, annotations={"semanticVersion": "1"})]
    report = validate_contract_expectations(scenario.tool_contracts, inventory)
    assert report["status"] == "passed"
    CommandAPI(Config(), output_dir=tmp_path / "runs").validate(scenario=scenario, tool_inventory=inventory)
    ordinary = Scenario.model_validate({"name": "ordinary", "actors": {"agent": [{"invoke": "reserve_inventory"}]}})
    assert ordinary.tool_contracts == {}


def test_contract_expectation_failure_is_clear(tmp_path: Path) -> None:
    scenario = {
        "name": "contracts",
        "actors": {"agent": [{"invoke": "reserve_inventory", "args": {}}]},
        "tool_contracts": {"reserve_inventory": {"required_inputs": ["quantity"]}},
    }
    with pytest.raises(Exception, match="inputs are not required"):
        CommandAPI(Config(), output_dir=tmp_path / "runs").validate(
            scenario=scenario, tool_inventory=[tool()]
        )


class _Page:
    def on(self, *_: object) -> None:
        pass


def test_observable_result_code_and_invariant_assertions() -> None:
    scenario = Scenario.model_validate({
        "name": "semantic",
        "actors": {"agent": [{"invoke": "reserve_inventory"}]},
        "tool_contracts": {"reserve_inventory": {
            "expected_result_codes": ["OK", "STALE_STATE"],
            "result_invariants": ["results.reserve_inventory.OK == 1"],
        }},
    })
    runner = ScenarioRunner(_Page(), scenario)  # type: ignore[arg-type]
    runner.result_counts = {"reserve_inventory": {"OK": 1}}
    runner.result_code_observations = {"reserve_inventory": ["OK"]}
    runner._check_tool_contract_assertions()
    assert all(event.type.endswith(".pass") for event in runner.recorder.run.events)
    runner.result_code_observations = {"reserve_inventory": ["BUSINESS_RULE_CHANGED"]}
    with pytest.raises(InvariantError, match="outside the declared contract"):
        runner._check_tool_contract_assertions()


def test_replay_contract_mismatch_raises_structured_error_before_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = [tool(input_schema={"type": "object", "properties": {"quantity": {"type": "integer"}}})]
    changed = [tool(input_schema={"type": "object", "properties": {"quantity": {"type": "integer"}}, "required": ["quantity"]})]

    class FakePage:
        url = "https://app.example/"

        async def goto(self, url: str) -> None:
            self.url = url

        def on(self, *_: object) -> None:
            pass

    class FakeClient:
        page = FakePage()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object):
            return None

    class FakeAdapter:
        def __init__(self, *_: object, **__: object):
            pass

        async def install(self) -> None:
            pass

        async def probe(self) -> dict:
            return {"api": {"available": True}, "runtime": {}}

        async def get_tools(self) -> list[dict]:
            return changed

    monkeypatch.setattr("webmcp_resilience.commands.BrowserClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr("webmcp_resilience.commands.WebMCPAdapter", FakeAdapter)
    expected = build_inventory_contract(baseline)["inventory_fingerprint"]
    with pytest.raises(ToolContractDriftError) as caught:
        asyncio.run(CommandAPI(Config(base_url="https://app.example"), output_dir=tmp_path / "runs").run(
            scenario={"name": "drift", "actors": {"agent": [{"invoke": "reserve_inventory"}]}},
            expected_tool_inventory_fingerprint=expected,
            expected_tool_inventory=baseline,
        ))
    assert caught.value.code == "tool_contract_drift"
    assert caught.value.details["changed_input_schemas"] == ["reserve_inventory"]


@pytest.mark.parametrize(
    ("include_descriptions", "rejected"),
    [(False, False), (True, True)],
)
def test_replay_description_drift_follows_recorded_fingerprint_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    include_descriptions: bool,
    rejected: bool,
) -> None:
    baseline = [tool(description="before")]
    changed = [tool(description="after")]
    contract = build_inventory_contract(
        baseline, include_descriptions=include_descriptions
    )
    scenario = Scenario.model_validate({
        "name": "description-drift",
        "actors": {"agent": [{"invoke": "reserve_inventory"}]},
    })
    requirements = CompatibilityRequirements.model_validate(
        scenario.compatibility["requires"]
    )
    approvals = [ApprovalRecord(authority="read_only", reason="default read-only policy")]
    saved = RunBundle(
        command="run",
        run_id=f"description-baseline-{str(include_descriptions).lower()}",
        scenario=scenario.model_dump(mode="json"),
        requirements=requirements,
        compatibility=Compatibility(
            groups=requirements,
            tool_inventory_fingerprint=contract["inventory_fingerprint"],
        ),
        tool_inventory=baseline,
        inventory_contract=contract,
        faults=[],
        approvals=approvals,
        execution={
            "adversarial": False,
            "seed": 0,
            "schedule": [{"offset_ms": 0, "actor": "agent", "action_index": 0}],
            "schedule_index": 0,
            "requirements": requirements.model_dump(mode="json"),
            "fault_configuration": [],
            "approval_policy": [item.model_dump(mode="json") for item in approvals],
        },
    )
    saved_path = saved.write(tmp_path / f"saved-{include_descriptions}.json")

    class FakePage:
        url = "https://app.example/"

        async def goto(self, url: str) -> None:
            self.url = url

        def on(self, *_: object) -> None:
            pass

        async def screenshot(self, **_: object) -> None:
            pass

    class FakeClient:
        page = FakePage()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_: object):
            return None

    class FakeAdapter:
        def __init__(self, *_: object, **__: object):
            pass

        async def install(self) -> None:
            pass

        async def probe(self) -> dict:
            return {"api": {"available": True}, "runtime": {}}

        async def get_tools(self) -> list[dict]:
            return changed

    class FakeRunner:
        def __init__(self, *_: object, **__: object):
            pass

        async def run(self, _schedule: object) -> TraceRun:
            return TraceRun(scenario="description-drift")

    monkeypatch.setattr("webmcp_resilience.commands.BrowserClient", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr("webmcp_resilience.commands.WebMCPAdapter", FakeAdapter)
    monkeypatch.setattr("webmcp_resilience.commands.ScenarioRunner", FakeRunner)
    # Replay must honor the policy recorded with the fingerprint, even if the
    # current process configuration differs.
    config = Config(
        base_url="https://app.example",
        tool_contract_include_descriptions=not include_descriptions,
    )
    replay = CommandAPI(config, output_dir=tmp_path / "runs").replay(
        saved_path, run_id=f"description-replay-{str(include_descriptions).lower()}"
    )
    if rejected:
        with pytest.raises(ToolContractDriftError) as caught:
            asyncio.run(replay)
        assert caught.value.details["descriptive_only_changes"] == ["reserve_inventory"]
        assert (
            caught.value.details["baseline_inventory_fingerprint"]
            != caught.value.details["current_inventory_fingerprint"]
        )
    else:
        result = asyncio.run(replay)
        assert result.command == "replay"
        assert result.result["passed"] is True
        assert result.tool_contract_drift["descriptive_only_changes"] == ["reserve_inventory"]
        assert (
            result.tool_contract_drift["baseline_inventory_fingerprint"]
            == result.tool_contract_drift["current_inventory_fingerprint"]
        )


def test_cli_and_mcp_replay_errors_keep_structured_drift_details(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = compare_tool_inventories(
        [tool()],
        [tool(input_schema={"type": "object", "properties": {"quantity": {"type": "integer"}}, "required": ["quantity"]})],
    )

    async def reject(*_args: object, **_kwargs: object) -> RunBundle:
        raise ToolContractDriftError(report)

    monkeypatch.setattr(CommandAPI, "replay", reject)
    monkeypatch.chdir(tmp_path)
    cli = CliRunner().invoke(app, ["replay", "saved.json", "--json"])
    assert cli.exit_code == 2
    cli_error = json.loads(cli.output)
    assert cli_error["error_code"] == "tool_contract_drift"
    assert cli_error["details"]["policy_impact"] == "breaking"

    adapter = LocalMCPControlAdapter(
        CommandAPI(Config(), output_dir=tmp_path / ".webmcp" / "runs"),
        AgentPolicy(tmp_path, "http://localhost:3000"),
    )
    # A valid source id is enough because replay itself is replaced at the
    # CommandAPI seam for this transport-envelope assertion.
    enveloped = asyncio.run(adapter.call_enveloped("replay_run", {"source_run_id": "saved"}))
    assert enveloped["ok"] is False
    assert enveloped["error"]["code"] == "tool_contract_drift"
    assert enveloped["error"]["details"]["changed_input_schemas"] == ["reserve_inventory"]


def test_cli_console_and_mcp_surface_the_same_contract_diff(tmp_path: Path) -> None:
    baseline_tools = [tool()]
    changed_tools = [tool(input_schema={"type": "object", "properties": {"quantity": {"type": "integer"}}, "required": ["quantity"]})]
    left = RunBundle(
        command="run", run_id="left", tool_inventory=baseline_tools,
        compatibility=Compatibility(tool_inventory_fingerprint=build_inventory_contract(baseline_tools)["inventory_fingerprint"]),
    )
    right = RunBundle(
        command="run", run_id="right", tool_inventory=changed_tools,
        compatibility=Compatibility(tool_inventory_fingerprint=build_inventory_contract(changed_tools)["inventory_fingerprint"]),
    )
    left_path = left.write(tmp_path / "left.json")
    right_path = right.write(tmp_path / "right.json")
    console_diff = bundle_comparison(left, right)
    assert console_diff["tool_contract_drift"]["policy_impact"] == "breaking"
    assert console_diff["compatibility_drift"]["changed"] is True
    assert console_diff["behavioural_drift"]["changed"] is False

    cli = CliRunner().invoke(app, ["diff", str(left_path), str(right_path), "--json"])
    assert cli.exit_code == 0, cli.output
    assert json.loads(cli.output)["tool_contract_drift"]["changed_input_schemas"] == ["reserve_inventory"]

    project = tmp_path / "project"
    project.mkdir()
    adapter = LocalMCPControlAdapter(
        CommandAPI(Config(), output_dir=project / ".webmcp" / "runs"),
        AgentPolicy(project, "http://localhost:3000"),
    )
    adapter.api.save(left)
    adapter.api.save(right)
    capabilities = asyncio.run(adapter.call("get_capabilities"))
    assert capabilities["tool_contract_drift"]["replay_rejection_code"] == "tool_contract_drift"
    assert capabilities["tool_contract_drift"]["descriptions_included"] is False
    mcp_diff = asyncio.run(adapter.call("diff_runs", {"left_run_id": "left", "right_run_id": "right"}))
    assert mcp_diff["diff"]["tool_contract_drift"]["policy_impact"] == "breaking"
    runs = asyncio.run(adapter.call("list_runs"))
    assert all("tool_contract_drift" in item for item in runs["runs"])


def test_cli_and_mcp_diff_use_saved_description_policy_not_runtime_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def recorded_bundle(run_id: str, description: str) -> RunBundle:
        inventory = [tool(description=description)]
        contract = build_inventory_contract(inventory, include_descriptions=True)
        return RunBundle(
            command="run",
            run_id=run_id,
            tool_inventory=inventory,
            inventory_contract=contract,
            compatibility=Compatibility(
                tool_inventory_fingerprint=contract["inventory_fingerprint"]
            ),
        )

    left = recorded_bundle("described-left", "before")
    right = recorded_bundle("described-right", "after")
    left_path = left.write(tmp_path / "described-left.json")
    right_path = right.write(tmp_path / "described-right.json")

    # With no project config the CLI runtime defaults descriptions off. The
    # saved bundles explicitly recorded them on and must remain authoritative.
    monkeypatch.chdir(tmp_path)
    cli = CliRunner().invoke(
        app, ["diff", str(left_path), str(right_path), "--json"]
    )
    assert cli.exit_code == 0, cli.output
    cli_drift = json.loads(cli.output)["tool_contract_drift"]
    assert cli_drift["fingerprint_policy"] == {"include_descriptions": True}
    assert cli_drift["descriptive_only_changes"] == ["reserve_inventory"]
    assert cli_drift["baseline_inventory_fingerprint"] == left.inventory_contract["inventory_fingerprint"]
    assert cli_drift["current_inventory_fingerprint"] == right.inventory_contract["inventory_fingerprint"]
    assert cli_drift["baseline_inventory_fingerprint"] != cli_drift["current_inventory_fingerprint"]

    project = tmp_path / "project"
    project.mkdir()
    adapter = LocalMCPControlAdapter(
        CommandAPI(
            Config(tool_contract_include_descriptions=False),
            output_dir=project / ".webmcp" / "runs",
        ),
        AgentPolicy(project, "http://localhost:3000"),
    )
    adapter.api.save(left)
    adapter.api.save(right)
    mcp = asyncio.run(adapter.call(
        "diff_runs",
        {"left_run_id": left.run_id, "right_run_id": right.run_id},
    ))
    mcp_drift = mcp["diff"]["tool_contract_drift"]
    assert mcp_drift == cli_drift


def test_diff_reports_saved_fingerprint_policy_mismatch_without_rehashing() -> None:
    left_inventory = [tool(description="before")]
    right_inventory = [tool(description="after")]
    left_contract = build_inventory_contract(
        left_inventory, include_descriptions=True
    )
    right_contract = build_inventory_contract(
        right_inventory, include_descriptions=False
    )
    left = RunBundle(
        command="run",
        run_id="policy-left",
        tool_inventory=left_inventory,
        inventory_contract=left_contract,
        compatibility=Compatibility(
            tool_inventory_fingerprint=left_contract["inventory_fingerprint"]
        ),
    )
    right = RunBundle(
        command="run",
        run_id="policy-right",
        tool_inventory=right_inventory,
        inventory_contract=right_contract,
        compatibility=Compatibility(
            tool_inventory_fingerprint=right_contract["inventory_fingerprint"]
        ),
    )

    report = bundle_comparison(left, right)
    drift = report["tool_contract_drift"]
    assert drift["status"] == "policy_mismatch"
    assert drift["policy_mismatch"] == {
        "baseline": {"include_descriptions": True},
        "current": {"include_descriptions": False},
    }
    assert drift["baseline_inventory_fingerprint"] == left_contract["inventory_fingerprint"]
    assert drift["current_inventory_fingerprint"] == right_contract["inventory_fingerprint"]
    assert report["compatibility_drift"]["changed"] is True


def test_handoff_json_surfaces_contract_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    bundle = RunBundle(
        command="run", run_id="handoff-contract",
        compatibility=Compatibility(tool_inventory_fingerprint="inventory-fingerprint"),
        tool_contract_drift={"version": "1.0", "status": "drift", "changed": True, "policy_impact": "breaking"},
    )
    source = bundle.write(tmp_path / "source.json")
    result = CliRunner().invoke(app, ["handoff", str(source), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["tool_inventory_fingerprint"] == "inventory-fingerprint"
    assert payload["tool_contract_drift"]["policy_impact"] == "breaking"

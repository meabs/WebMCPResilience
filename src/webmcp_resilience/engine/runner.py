import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

import yaml
from playwright.async_api import Page

from ..browser.webmcp_adapter import WebMCPAdapter, WebMCPUnavailable
from ..faults import build_effects
from ..faults.cancellation import cancels
from ..faults.duplicate import duplicates
from ..faults.latency import delay_for
from ..faults.http import http_route, response_handler
from ..faults.navigation import navigates, navigation_url
from ..faults.timeout import timeout_for
from ..models.scenario import Scenario, TimedAction
from ..models.bundle import redact_recursive
from ..models.trace import TraceRun
from ..security import resolve_navigation_url
from ..trace.recorder import Recorder
from .explorer import ScheduledAction
from .invariants import InvariantError, check
from .state import observe, resolve


class ScenarioRunner:
    def __init__(self, page: Page, scenario: Scenario, state_script: str | None = None, from_origins: list[str] | None = None, allow_mutations: bool = False, *, base_url: str | None = None, webmcp_profile: str = "auto") -> None:
        self.page, self.scenario = page, scenario
        self.adapter = WebMCPAdapter(page, state_script, from_origins, webmcp_profile)
        self.faults = build_effects(scenario.faults)
        required_groups = {"scenario": "1", "fault_model": "1", "invariant_model": "1", "trace_model": "1", "browser_webmcp_adapter": "1"}
        declared = scenario.compatibility.get("requires")
        if isinstance(declared, dict):
            required_groups.update(declared)
        self.recorder = Recorder(scenario.name, required_groups)
        self.invocations: dict[str, asyncio.Task[Any]] = {}
        self.invocation_names: dict[str, str] = {}
        self.invocation_descriptors: dict[str, dict[str, Any]] = {}
        self.effective_dispatch: list[dict[str, Any]] = []
        self.background: list[asyncio.Task[Any]] = []
        self.failure: dict[str, Any] | None = None
        self.allow_mutations = allow_mutations
        self.base_url = base_url
        self.state: dict[str, Any] = {}
        self.result_counts: dict[str, dict[str, int]] = {}
        self.result_code_observations: dict[str, list[str | None]] = {}
        self.tools: dict[str, dict[str, Any]] = {}
        self._tool_candidates: dict[str, list[dict[str, Any]]] = {}
        self._adapter_stale = False
        page.on("framenavigated", self._on_navigation)

    def _on_navigation(self, frame: Any) -> None:
        if frame == self.page.main_frame:
            self._adapter_stale = True

    async def _ensure_adapter(self) -> None:
        if self._adapter_stale:
            await self.page.wait_for_load_state("domcontentloaded")
            await self.adapter.install()
            self._set_tools(await self.adapter.get_tools())
            self._adapter_stale = False
            self.recorder.add("system", "adapter.reattached", data={"tool_count": len(self.tools)})

    def _set_tools(self, tools: list[dict[str, Any]]) -> None:
        candidates: dict[str, list[dict[str, Any]]] = {}
        for tool in tools:
            candidates.setdefault(str(tool.get("name")), []).append(tool)
        self._tool_candidates = candidates
        # Keep the historical mapping for unique names.  Ambiguous names are
        # resolved explicitly by _descriptor_for instead of silently picking
        # whichever frame happened to be returned last.
        self.tools = {name: values[0] for name, values in candidates.items() if len(values) == 1}

    def _descriptor_for(self, name: str, action: TimedAction | None = None) -> dict[str, Any]:
        matches = self._tool_candidates.get(name, [])
        if not matches:
            raise WebMCPUnavailable(f"WebMCP tool not discovered: {name}")
        if action and (action.tool_origin or action.tool_frame):
            matches = [item for item in matches if
                       (not action.tool_origin or (item.get("identity") or {}).get("origin") == action.tool_origin)
                       and (not action.tool_frame or (item.get("identity") or {}).get("frame") == action.tool_frame)]
            if not matches:
                raise WebMCPUnavailable(f"WebMCP tool {name!r} has no match for the requested origin/frame identity")
        if len(matches) > 1:
            identities = [item.get("identity") for item in matches]
            raise WebMCPUnavailable(f"WebMCP tool {name!r} is ambiguous; qualify the discovered identity: {identities}")
        return matches[0]

    async def _invoke_adapter(self, name: str, args: dict[str, Any], invocation_id: str,
                              descriptor: dict[str, Any]) -> Any:
        """Call old test adapters while passing handles to the real adapter."""
        if descriptor.get("handleId"):
            return await self.adapter.invoke_tool(name, args, invocation_id, descriptor)
        return await self.adapter.invoke_tool(name, args, invocation_id)

    async def _start_adapter(self, name: str, args: dict[str, Any], invocation_id: str,
                             descriptor: dict[str, Any]) -> None:
        if descriptor.get("handleId"):
            await self.adapter.start_tool(name, args, invocation_id, descriptor)
        else:
            await self.adapter.start_tool(name, args, invocation_id)

    async def run(self, schedule: list[ScheduledAction] | None = None) -> TraceRun:
        await self.adapter.install()
        self.recorder.add("system", "scenario.start")
        await self._install_http_faults()
        required = {action.invoke or action.retry for actions in self.scenario.actors.values() for action in actions if action.invoke or action.retry}
        if self.scenario.state:
            required.add(self.scenario.state.tool)
        discovered = await self.adapter.wait_for_tools(required)
        self._set_tools(discovered)
        for tool in discovered:
            self.recorder.add("system", "tool.discovered", name=tool.get("name"), data=tool)
        await self._record_lifecycle()
        scheduled = schedule or [(action.offset_ms, actor, action) for actor, actions in self.scenario.actors.items() for action in actions]
        grouped: dict[int, list[ScheduledAction]] = {}
        for item in scheduled:
            grouped.setdefault(item[0], []).append(item)
        action_tasks = [
            asyncio.create_task(self._run_group(offset, entries))
            for offset, entries in sorted(grouped.items())
        ]
        action_outcomes = await asyncio.gather(*action_tasks, return_exceptions=True)
        seen: set[int] = set()
        invocation_outcomes: list[Any] = []
        while pending := [task for task in self.invocations.values() if id(task) not in seen]:
            seen.update(id(task) for task in pending)
            invocation_outcomes.extend(await asyncio.gather(*pending, return_exceptions=True))
        background_outcomes = await asyncio.gather(*self.background, return_exceptions=True)
        for outcome in [*action_outcomes, *background_outcomes]:
            if isinstance(outcome, Exception):
                raise outcome
        for outcome in invocation_outcomes:
            if isinstance(outcome, Exception):
                raise outcome
        await self._check_invariants("system")
        for expression in self.scenario.result_invariants:
            check(expression, {"results": self.result_counts})
            self.recorder.add("system", "result_invariant.pass", data={"expression": expression, "results": self.result_counts})
        self._check_tool_contract_assertions()
        self.recorder.add("system", "scenario.end")
        return self.recorder.run

    def _check_tool_contract_assertions(self) -> None:
        """Evaluate only scenario-declared, observable result contracts."""
        for name, expectation in self.scenario.tool_contracts.items():
            observed = self.result_code_observations.get(name, [])
            if expectation.expected_result_codes and observed:
                allowed = set(expectation.expected_result_codes)
                unexpected = sorted({code if code is not None else "<missing>" for code in observed if code not in allowed})
                if unexpected:
                    message = (
                        f"{name} returned result codes outside the declared contract: {unexpected}; "
                        f"expected one of {sorted(allowed)}"
                    )
                    self.recorder.add(
                        "system", "tool_contract.assertion.fail", name=name,
                        data={"assertion": "expected_result_codes", "expected": sorted(allowed), "observed": observed, "error": message},
                    )
                    raise InvariantError(message)
                self.recorder.add(
                    "system", "tool_contract.assertion.pass", name=name,
                    data={"assertion": "expected_result_codes", "expected": sorted(allowed), "observed": observed},
                )
            for expression in expectation.result_invariants:
                try:
                    check(expression, {"results": self.result_counts})
                    self.recorder.add(
                        "system", "tool_contract.assertion.pass", name=name,
                        data={"assertion": "result_invariant", "expression": expression, "results": self.result_counts},
                    )
                except (InvariantError, KeyError) as error:
                    self.recorder.add(
                        "system", "tool_contract.assertion.fail", name=name,
                        data={"assertion": "result_invariant", "expression": expression, "results": self.result_counts, "error": str(error)},
                    )
                    raise InvariantError(str(error)) from error

    async def _install_http_faults(self) -> None:
        for fault in self.faults:
            pattern = http_route(fault)
            if pattern:
                await self.page.route(pattern, response_handler(fault))
                self.recorder.add("system", "fault.injected", name="http_error", data={"url": pattern, "status": fault.status})

    async def _run_at(self, offset: int, actor: str, action: TimedAction) -> None:
        await asyncio.sleep(max(0, offset - round((time.monotonic() - self.recorder.started) * 1000)) / 1000)
        self.recorder.add(actor, "scheduler.yield", data={"point": "before_action", "offset_ms": offset})
        await asyncio.sleep(0)
        await self._execute(actor, action)
        await self._record_lifecycle()
        await self._check_invariants(actor)

    async def _run_group(self, offset: int, entries: list[ScheduledAction]) -> None:
        """Run one scheduler point, starting eligible tools in page context first.

        Playwright commands on one Page are transport-serialized. For a
        same-offset group, starting the tool promise in the browser before
        issuing the UI command preserves the actor-level concurrency promised
        by the scenario DSL. Faulted invocations retain the normal path so
        explicit before-invoke faults cannot be bypassed by prestarting.
        """
        if len(entries) == 1:
            await self._run_at(*entries[0])
            return
        await asyncio.sleep(max(0, offset - round((time.monotonic() - self.recorder.started) * 1000)) / 1000)
        for _, actor, _ in entries:
            self.recorder.add(actor, "scheduler.yield", data={"point": "before_action", "offset_ms": offset})
        await asyncio.sleep(0)
        deferred: list[tuple[str, str, str, dict[str, Any]]] = []
        prestarted_actions: set[int] = set()
        can_prestart = not any(action.cancel or action.action == "navigate" for _, _, action in entries)
        for actor, action in ((actor, action) for _, actor, action in entries):
            if can_prestart and (action.invoke or action.retry) and self._can_prestart(action.invoke or action.retry):
                await self._execute(actor, action, prestart_tool=True, deferred=deferred)
                prestarted_actions.add(id(action))
        if deferred:
            self.recorder.add(
                "system",
                "scheduler.concurrent_dispatch",
                data={"offset_ms": offset, "actors": [actor for _, actor, _ in entries], "browser_side_tool_start": True},
            )
        for _, actor, action in entries:
            if action.invoke or action.retry:
                if id(action) in prestarted_actions:
                    continue
            await self._execute(actor, action)
        for actor, invocation_id, name, arguments in deferred:
            self.invocations[invocation_id] = asyncio.create_task(
                self._invoke(actor, invocation_id, name, arguments, prestarted=True)
            )
        for _, actor, _ in entries:
            await self._record_lifecycle()
            await self._check_invariants(actor)

    def _can_prestart(self, name: str) -> bool:
        return not any(self._fault_due(fault, name, "before_invoke") for fault in self.faults)

    async def _record_lifecycle(self) -> None:
        for event in await self.adapter.drain_lifecycle_events():
            self.recorder.add("system", "tool.change", data=event)
        # A lifecycle event is a cache invalidation signal.  Refreshing here
        # keeps dynamic registration, changed annotations, and navigation from
        # being represented using stale descriptors.
        refreshed = await self.adapter.get_tools()
        self._set_tools(refreshed)
        self.recorder.add("system", "tool.inventory.refresh", data={
            "count": len(refreshed),
            "names": [tool.get("name") for tool in refreshed],
        })

    async def _execute(
        self,
        actor: str,
        action: TimedAction,
        *,
        prestart_tool: bool = False,
        deferred: list[tuple[str, str, str, dict[str, Any]]] | None = None,
    ) -> str | None:
        if not self.allow_mutations and (action.action in {"click", "fill", "select"} or action.cancel is not None):
            raise PermissionError(
                "policy_denied: read-only policy blocks state-changing UI and cancellation actions"
            )
        if action.invoke or action.retry:
            await self._ensure_adapter()
            name, event = action.invoke or action.retry, "tool.invoke" if action.invoke else "tool.retry"
            descriptor = self._descriptor_for(name, action)
            if not self.allow_mutations and not (descriptor.get("annotations") or {}).get("readOnlyHint", False):
                raise PermissionError(f"{name} can mutate application state; rerun with --allow-mutations")
            if self.scenario.state:
                self.state = await observe(self.adapter, self.scenario.state, f"state-{uuid.uuid4()}")
                self.recorder.add("system", "state.observed", state=self.state)
            arguments = resolve(action.args, self.state)
            invocation_id = str(uuid.uuid4())
            self.invocation_names[invocation_id] = name
            self.invocation_descriptors[invocation_id] = descriptor
            self.recorder.add(actor, "action.requested", name=name, invocation_id=invocation_id,
                              data={"operation": event, "args": arguments})
            self.recorder.add(actor, event, name=name, invocation_id=invocation_id, data={"args": arguments})
            if prestart_tool:
                await self._start_adapter(name, arguments, invocation_id, descriptor)
                self.effective_dispatch.append({"invocation_id": invocation_id, "actor": actor, "name": name, "mode": "prestarted", "handle_id": descriptor.get("handleId")})
                self.recorder.add(actor, "action.dispatched", name=name, invocation_id=invocation_id, data={"mode": "prestarted", "handle_id": descriptor.get("handleId")})
                if deferred is None:
                    self.invocations[invocation_id] = asyncio.create_task(self._invoke(actor, invocation_id, name, arguments, prestarted=True))
                else:
                    deferred.append((actor, invocation_id, name, arguments))
            else:
                self.effective_dispatch.append({"invocation_id": invocation_id, "actor": actor, "name": name, "mode": "task", "handle_id": descriptor.get("handleId")})
                self.recorder.add(actor, "action.dispatched", name=name, invocation_id=invocation_id, data={"mode": "task", "handle_id": descriptor.get("handleId")})
                self.invocations[invocation_id] = asyncio.create_task(self._invoke(actor, invocation_id, name, arguments))
            for fault in self.faults:
                if self._fault_due(fault, name, "before_invoke") and duplicates(fault, name):
                    duplicate_id = str(uuid.uuid4())
                    self.recorder.add("system", "fault.injected", name="duplicate_invocation", data={"tool": name})
                    self.recorder.add(actor, "tool.invoke", name=name, invocation_id=duplicate_id, data={"args": arguments, "duplicate": True})
                    self.invocations[duplicate_id] = asyncio.create_task(self._invoke(actor, duplicate_id, name, arguments, fault_generated=True))
                elif self._fault_due(fault, name, "before_invoke") and cancels(fault, name):
                    self.recorder.add("system", "fault.injected", name="cancellation", data={"tool": name})
                    self.background.append(asyncio.create_task(self._cancel_after_yield(invocation_id, name)))
                elif self._fault_due(fault, name, "before_invoke") and navigates(fault):
                    self.recorder.add("system", "fault.injected", name="navigation", data={"url": fault.url})
                    self.background.append(asyncio.create_task(self.page.goto(self._navigation_target(navigation_url(fault)) or self.page.url)))
            return invocation_id
        if action.cancel:
            request_id = str(uuid.uuid4())
            self.recorder.add(actor, "action.requested", name=action.cancel, invocation_id=request_id,
                              data={"operation": "tool.cancel", "target_tool": action.cancel})
            # ``cancel`` names the declared target tool.  Never cancel the
            # first pending task belonging to a different actor/tool.
            for invocation_id, task in self.invocations.items():
                if self.invocation_names.get(invocation_id) == action.cancel and not task.done():
                    await self.adapter.cancel(invocation_id)
                    task.cancel()
                    self.recorder.add(actor, "tool.cancel", name=action.cancel, invocation_id=invocation_id)
                    self.recorder.add(actor, "action.dispatched", name=action.cancel, invocation_id=request_id,
                                      data={"operation": "tool.cancel", "target_invocation_id": invocation_id})
                    self.recorder.add(actor, "action.completed", name=action.cancel, invocation_id=request_id,
                                      data={"outcome": "cancelled", "target_invocation_id": invocation_id})
                    return
            raise RuntimeError(f"no in-flight invocation exists to cancel for {action.cancel!r}")
        request_id = str(uuid.uuid4())
        self.recorder.add(actor, "action.requested", invocation_id=request_id,
                          data={"operation": f"ui.{action.action}", "action": action.model_dump(exclude_none=True)})
        self.recorder.add(actor, "action.dispatched", invocation_id=request_id,
                          data={"operation": f"ui.{action.action}"})
        try:
            await self._ui(actor, action)
        except Exception as error:
            self.recorder.add(actor, "action.completed", invocation_id=request_id,
                              data={"outcome": "error", "error_type": type(error).__name__})
            raise
        self.recorder.add(actor, "action.completed", invocation_id=request_id,
                          data={"outcome": "result"})
        return None

    async def _invoke(
        self,
        actor: str,
        invocation_id: str,
        name: str,
        args: dict[str, Any],
        *,
        fault_generated: bool = False,
        prestarted: bool = False,
    ) -> None:
        try:
            for fault in self.faults:
                if self._fault_due(fault, name, "before_invoke") and delay_for(fault, name):
                    self.recorder.add("system", "fault.injected", name="latency", data={"tool": name, "duration_ms": fault.duration_ms})
                    await asyncio.sleep(fault.duration_ms / 1000)
                    self.recorder.add(actor, "scheduler.yield", data={"point": "after_latency", "tool": name})
                    await asyncio.sleep(0)
            timeout_ms = next((value for fault in self.faults if self._fault_due(fault, name, "before_invoke") and (value := timeout_for(fault, name)) is not None), None)
            if prestarted:
                result = await self.adapter.await_started_tool(invocation_id)
            elif timeout_ms is not None:
                self.recorder.add("system", "fault.injected", name="timeout", data={"tool": name, "duration_ms": timeout_ms})
                try:
                    result = await asyncio.wait_for(
                        self._invoke_adapter(name, args, invocation_id, self.invocation_descriptors.get(invocation_id, {})),
                        timeout_ms / 1000,
                    )
                except asyncio.TimeoutError:
                    # Cancelling the Python wait alone does not establish what
                    # happened in the browser.  Send the same invocation's
                    # AbortSignal before exposing the timeout to the caller.
                    await self.adapter.cancel(invocation_id)
                    self.recorder.add(actor, "tool.timeout", name=name, invocation_id=invocation_id,
                                      data={"duration_ms": timeout_ms, "browser_abort_requested": True})
                    raise
            else:
                result = await self._invoke_adapter(name, args, invocation_id, self.invocation_descriptors.get(invocation_id, {}))
            if isinstance(result, str):
                try: result = json.loads(result)
                except ValueError: pass
            if isinstance(result, dict) and "code" in result:
                code = str(result["code"])
                bucket = self.result_counts.setdefault(name, {})
                bucket[code] = bucket.get(code, 0) + 1
            self.result_code_observations.setdefault(name, []).append(
                str(result["code"]) if isinstance(result, dict) and "code" in result else None
            )
            self.recorder.add(actor, "tool.result", name=name, invocation_id=invocation_id, data={"result": result})
            self.recorder.add(actor, "action.completed", name=name, invocation_id=invocation_id,
                              data={"outcome": "result"})
            # A duplicate is an injected invocation, not a new opportunity to
            # inject the same declared fault.  Without this marker an
            # after_invoke duplicate recursively schedules itself forever.
            if not fault_generated:
                await self._apply_after_invoke_faults(actor, name, args)
        except asyncio.CancelledError:
            self.recorder.add(actor, "tool.cancel", name=name, invocation_id=invocation_id)
            self.recorder.add(actor, "action.completed", name=name, invocation_id=invocation_id,
                              data={"outcome": "cancelled"})
            raise
        except Exception as error:
            self.recorder.add(actor, "tool.error", name=name, invocation_id=invocation_id, data={"error": str(error)})
            self.recorder.add(actor, "action.completed", name=name, invocation_id=invocation_id,
                              data={"outcome": "error", "error_type": type(error).__name__})
            raise

    async def _cancel_after_yield(self, invocation_id: str, name: str) -> None:
        await asyncio.sleep(0)
        task = self.invocations.get(invocation_id)
        if task and not task.done():
            await self.adapter.cancel(invocation_id)
            task.cancel()
            self.recorder.add("system", "tool.cancel", name=name, invocation_id=invocation_id)

    async def _apply_after_invoke_faults(self, actor: str, name: str, args: dict[str, Any]) -> None:
        """Apply the explicitly supported post-result timing without implicit fallbacks."""
        for fault in self.faults:
            if not self._fault_due(fault, name, "after_invoke"):
                continue
            if delay_for(fault, name):
                self.recorder.add("system", "fault.injected", name="latency", data={"tool": name, "duration_ms": fault.duration_ms, "at": "after_invoke"})
                await asyncio.sleep(fault.duration_ms / 1000)
            elif duplicates(fault, name):
                duplicate_id = str(uuid.uuid4())
                self.recorder.add("system", "fault.injected", name="duplicate_invocation", data={"tool": name, "at": "after_invoke"})
                self.recorder.add(actor, "tool.invoke", name=name, invocation_id=duplicate_id, data={"args": args, "duplicate": True})
                self.invocations[duplicate_id] = asyncio.create_task(self._invoke(actor, duplicate_id, name, args, fault_generated=True))
            elif navigates(fault):
                self.recorder.add("system", "fault.injected", name="navigation", data={"url": fault.url, "at": "after_invoke"})
                await self.page.goto(self._navigation_target(navigation_url(fault)) or self.page.url)

    @staticmethod
    def _fault_due(fault: Any, tool: str, point: str) -> bool:
        """A fault is applied at an explicit scheduler point, never implicitly."""
        return (fault.tool is None or fault.tool == tool) and fault.at == point

    async def _ui(self, actor: str, action: TimedAction) -> None:
        if action.action == "wait":
            await self.page.wait_for_timeout(int(action.value or "0"))
        elif action.action == "navigate":
            await self.page.goto(self._navigation_target(action.value) or "")
        elif action.action == "click":
            await self.page.locator(action.selector or "").click()
        elif action.action == "fill":
            await self.page.locator(action.selector or "").fill(action.value or "")
        elif action.action == "select":
            await self.page.locator(action.selector or "").select_option(action.value or "")
        else:
            raise RuntimeError(f"unsupported UI action {action.action!r}")
        self.recorder.add(actor, f"ui.{action.action}", data=action.model_dump(exclude_none=True))

    def _navigation_target(self, value: str | None) -> str | None:
        if value is None or self.base_url is None:
            return value
        # CommandAPI validates origin policy before constructing the browser;
        # this second resolution ensures Playwright receives the same URL that
        # was checked rather than a browser-normalized relative reference.
        return resolve_navigation_url(value, self.base_url)

    async def _check_invariants(self, actor: str) -> None:
        if not self.scenario.invariants:
            return
        state = await self.adapter.get_state()
        self.recorder.add("system", "state.observed", state=state)
        for expression in self.scenario.invariants:
            try:
                check(expression, state)
                self.recorder.add("system", "invariant.pass", data={"expression": expression}, state=state)
            except (InvariantError, KeyError) as error:
                self.failure = {"expression": expression, "observed": state, "error": str(error)}
                self.recorder.add("system", "invariant.fail", data={"expression": expression, "error": str(error)}, state=state)
                raise InvariantError(str(error)) from error

    def write_failure(self, destination: Path, schedule: list[dict[str, Any]] | None = None, *, run_id: str, requirements: dict[str, str]) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {"schema_version": "2.0", "run_id": run_id, "requirements": requirements, "compatibility": self.recorder.run.compatibility, "scenario": self.scenario.model_dump(mode="json"), "trace": self.recorder.run.model_dump(mode="json"), "execution": {"schedule": schedule}}
        if self.failure:
            record["failed_invariant"] = {"expression": self.failure["expression"]}
            record["observed"] = self.failure["observed"]
        destination.write_text(yaml.safe_dump(redact_recursive(record), sort_keys=False))
        return destination

(() => {
  const context = () => document.modelContext ?? navigator.modelContext;
  const requireContext = () => {
    const value = context();
    if (!value) throw new Error("WebMCP is unavailable: document.modelContext was not exposed by this browser/page.");
    return value;
  };
  const argumentMode = (profile, host) => {
    if (profile === 'native-object') return 'object';
    if (profile === 'legacy-string') return 'string';
    // ``auto`` follows the host identity established by the preflight probe.
    // The bundled compatibility host retains its legacy JSON-string contract;
    // every other exposed WebMCP host uses the native object contract.
    return host?.__webmcpResilienceCompatibilityHost ? 'string' : 'object';
  };
  window.__webmcp_resilience = {
    lifecycle: [],
    pendingInvocations: new Map(),
    toolHandles: new Map(),
    async getTools(fromOrigins = [], profile = 'auto') {
      this.defaultOrigins = [...fromOrigins];
      const options = fromOrigins.length ? { fromOrigins } : undefined;
      const host = requireContext();
      const tools = await host.getTools(options);
      // Resolve once from the selected profile/host; never retry a mutating
      // call using a second argument encoding.
      const selectedArgumentMode = argumentMode(profile, host);
      return Array.from(tools, (tool, index) => {
        const handleId = `${location.origin}::${window === window.top ? 'top' : location.href}::${tool.name}::${index}`;
        this.toolHandles.set(handleId, tool);
        return {
        name: tool.name,
        description: tool.description,
        inputSchema: typeof tool.inputSchema === 'string' ? JSON.parse(tool.inputSchema) : tool.inputSchema,
        outputSchema: typeof tool.outputSchema === 'string' ? JSON.parse(tool.outputSchema) : tool.outputSchema,
        annotations: tool.annotations,
        // Explicit scenario expectations may validate this declaration. It is
        // evidence only and is not part of the default compatibility hash.
        semanticVersion: tool.semanticVersion,
        handleId,
        identity: { handleId, name: tool.name, origin: location.origin, frame: window === window.top ? 'top' : location.href },
        argumentMode: selectedArgumentMode,
      };
      });
    },
    async invokeTool(name, args, invocationId, handleId = null, profile = 'auto') {
      const host = requireContext();
      let tool = handleId ? this.toolHandles.get(handleId) : null;
      if (!tool) {
        const discovered = await this.getTools(this.defaultOrigins || [], profile);
        const matches = discovered.filter(candidate => candidate.name === name);
        if (matches.length !== 1) throw new Error(matches.length ? `WebMCP tool ${name} is ambiguous; use its discovered handle.` : `WebMCP tool not discovered: ${name}`);
        tool = this.toolHandles.get(matches[0].handleId);
      }
      if (!tool) throw new Error(`WebMCP tool not discovered: ${name}`);
      const controller = new AbortController();
      this.controllers.set(invocationId, controller);
      try {
        const mode = argumentMode(profile, host);
        const input = mode === 'object' ? (args ?? {}) : JSON.stringify(args ?? {});
        return await host.executeTool(tool, input, { signal: controller.signal });
      } finally {
        this.controllers.delete(invocationId);
      }
    },
    startTool(name, args, invocationId, handleId = null, profile = 'auto') {
      if (this.pendingInvocations.has(invocationId)) throw new Error(`WebMCP invocation already started: ${invocationId}`);
      const pending = this.invokeTool(name, args, invocationId, handleId, profile);
      // A rejected promise is intentionally retained for awaitTool(), but a
      // detached browser-side promise must also not become an unhandled error.
      pending.catch(() => {});
      this.pendingInvocations.set(invocationId, pending);
      return true;
    },
    async awaitTool(invocationId) {
      const pending = this.pendingInvocations.get(invocationId);
      if (!pending) throw new Error(`WebMCP invocation was not started: ${invocationId}`);
      try {
        return await pending;
      } finally {
        this.pendingInvocations.delete(invocationId);
      }
    },
    async cancel(invocationId) {
      const controller = this.controllers.get(invocationId);
      if (!controller) return false;
      controller.abort();
      return true;
    },
    async getState(script) {
      if (!script) return {};
      return await (0, eval)(script);
    },
    drainLifecycle() {
      const events = this.lifecycle.splice(0);
      return events;
    },
    probe() {
      const host = context();
      const compatibilityHost = Boolean(host?.__webmcpResilienceCompatibilityHost);
      const nativeHost = Boolean(host) && !compatibilityHost;
      const topOrigin = (() => { try { return window.top?.location?.origin || null; } catch (_) { return null; } })();
      const frames = Array.from(document.querySelectorAll('iframe')).map((frame, index) => ({
        index, src: frame.src || null, sandbox: frame.getAttribute('sandbox'), allow: frame.getAttribute('allow'),
        sameOrigin: (() => { try { return frame.contentWindow?.location.origin === location.origin; } catch (_) { return false; } })()
      }));
      const policy = document.permissionsPolicy || document.featurePolicy;
      const policyFeatures = policy?.features?.() || [];
      const policyFeature = policyFeatures.includes('tools') ? 'tools'
        : policyFeatures.includes('model-context') ? 'model-context' : null;
      let modelContextAllowed = null;
      if (policyFeature && typeof policy?.allowsFeature === 'function') {
        try { modelContextAllowed = policy.allowsFeature(policyFeature); } catch (_) { /* probe remains non-fatal */ }
      }
      const tools = host?.getTools ? [] : null; // Never call it: this evidence probe is non-mutating and API-shape only.
      return {
        api: {
          available: Boolean(host), location: document.modelContext ? 'document.modelContext' : navigator.modelContext ? 'navigator.modelContext' : null,
          mode: nativeHost ? 'native' : compatibilityHost ? 'compatibility_host' : 'unavailable',
          native: nativeHost,
          compatibilityHost,
          getTools: typeof host?.getTools === 'function', executeTool: typeof host?.executeTool === 'function', toolchange: typeof host?.addEventListener === 'function',
          cancellation: typeof AbortController === 'function', declarativeOrImperative: tools === null ? 'unavailable' : 'unknown-until-inventory'
        },
        document: { origin: location.origin, url: location.href, secureContext: isSecureContext, crossOriginIsolated, visibilityState: document.visibilityState, isTopLevel: window.top === window, topOrigin, sameOriginWithTop: topOrigin === location.origin, iframeCount: frames.length, frames },
        permissionsPolicy: { available: Boolean(policy), features: policyFeatures, feature: policyFeature, modelContextAllowed },
        runtime: { userAgent: navigator.userAgent, platform: navigator.platform, headless: /HeadlessChrome/i.test(navigator.userAgent), abortController: typeof AbortController === 'function', navigation: typeof location.assign === 'function' },
        lifecycle: { toolchangeListenerSupported: typeof host?.addEventListener === 'function' }
      };
    },
    controllers: new Map(),
  };
  // Installation must remain possible on an unsupported browser so preflight
  // can emit evidence instead of failing before it has inspected anything.
  context()?.addEventListener?.('toolchange', () => {
    window.__webmcp_resilience.lifecycle.push({ type: 'toolchange', timestamp: Date.now() });
  });
})();

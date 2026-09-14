import {
  AbsoluteFill,
  Easing,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

const palette = {
  canvas: "#07111f",
  panel: "#11243a",
  border: "#24415e",
  ink: "#e8f0f8",
  muted: "#9fb2c5",
  cyan: "#6ee7f2",
  lime: "#a3e635",
  red: "#fb7185",
};

const clamp = (value: number) => Math.max(0, Math.min(1, value));

const reveal = (frame: number, start: number, duration = 12) =>
  interpolate(frame, [start, start + duration], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.cubic),
  });

const TerminalLine: React.FC<{
  children: React.ReactNode;
  color?: string;
  frame: number;
  start: number;
}> = ({ children, color = palette.ink, frame, start }) => {
  const opacity = reveal(frame, start, 8);
  return (
    <div
      style={{
        color,
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        fontSize: 26,
        lineHeight: 1.55,
        opacity,
        translate: `0 ${interpolate(opacity, [0, 1], [8, 0])}px`,
      }}
    >
      {children}
    </div>
  );
};

const ActorCard: React.FC<{
  label: string;
  action: string;
  color: string;
  frame: number;
  start: number;
  align: "left" | "right";
}> = ({ label, action, color, frame, start, align }) => {
  const { fps } = useVideoConfig();
  const entrance = spring({ frame: frame - start, fps, config: { damping: 18 } });
  return (
    <div
      style={{
        width: 310,
        padding: "26px 28px",
        borderRadius: 20,
        background: palette.panel,
        border: `2px solid ${color}`,
        boxShadow: `0 16px 44px ${color}22`,
        opacity: entrance,
        translate: `${interpolate(entrance, [0, 1], [align === "left" ? -80 : 80, 0])}px 0`,
      }}
    >
      <div style={{ color, fontSize: 20, fontWeight: 800, letterSpacing: 2, textTransform: "uppercase" }}>
        {label}
      </div>
      <div style={{ color: palette.ink, fontSize: 29, fontWeight: 700, marginTop: 11 }}>{action}</div>
    </div>
  );
};

const RacePath: React.FC<{ frame: number }> = ({ frame }) => {
  const progress = reveal(frame, 98, 42);
  const pulse = 0.35 + 0.65 * Math.sin(frame / 5) ** 2;
  return (
    <>
      <div
        style={{
          position: "absolute",
          left: 320,
          right: 320,
          top: 310,
          height: 4,
          background: `linear-gradient(90deg, ${palette.cyan} ${progress * 50}%, ${palette.lime} ${progress * 100}%, ${palette.border} ${progress * 100}%)`,
          opacity: progress,
        }}
      />
      <div
        style={{
          position: "absolute",
          top: 286,
          left: 610,
          width: 60,
          height: 60,
          borderRadius: "50%",
          background: palette.red,
          border: `10px solid ${palette.canvas}`,
          boxShadow: `0 0 0 ${Math.round(14 * pulse)}px ${palette.red}22`,
          opacity: reveal(frame, 133, 8),
        }}
      />
    </>
  );
};

export const DemoRace: React.FC = () => {
  const frame = useCurrentFrame();
  const showRace = frame >= 82 && frame < 190;
  const showFailure = frame >= 172 && frame < 275;
  const showReplay = frame >= 258;
  const sceneOpacity = (start: number, end: number) =>
    clamp(Math.min(reveal(frame, start, 12), 1 - reveal(frame, end, 12)));

  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(circle at 50% -15%, #14385b 0%, ${palette.canvas} 50%)`,
        color: palette.ink,
        fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          backgroundImage: "linear-gradient(#ffffff06 1px, transparent 1px), linear-gradient(90deg, #ffffff06 1px, transparent 1px)",
          backgroundSize: "44px 44px",
          opacity: 0.4,
        }}
      />

      <div style={{ position: "absolute", top: 68, left: 88, right: 88, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ color: palette.cyan, fontSize: 25, fontWeight: 800, letterSpacing: 3 }}>WEBMCP RESILIENCE</div>
        <div style={{ color: palette.muted, fontFamily: "ui-monospace, monospace", fontSize: 19 }}>deterministic browser resilience tests</div>
      </div>

      <div style={{ opacity: sceneOpacity(0, 82), position: "absolute", inset: "145px 100px 72px", display: "flex", flexDirection: "column", alignItems: "center" }}>
        <div style={{ fontSize: 64, fontWeight: 850, textAlign: "center", lineHeight: 1.05, maxWidth: 1000, opacity: reveal(frame, 8) }}>
          Find the race before it reaches production.
        </div>
        <div style={{ marginTop: 38, width: 890, padding: "30px 36px", borderRadius: 20, background: "#050b14", border: `1px solid ${palette.border}` }}>
          <TerminalLine frame={frame} start={28} color={palette.cyan}>
            <span style={{ color: palette.muted }}>$ </span>.venv/bin/webmcp demo-race
          </TerminalLine>
          <TerminalLine frame={frame} start={49} color={palette.muted}>
            preflight → validate → seeded adversarial run → reduction → replay
          </TerminalLine>
        </div>
      </div>

      {showRace ? (
        <div style={{ opacity: sceneOpacity(82, 190), position: "absolute", inset: "150px 105px 92px" }}>
          <div style={{ textAlign: "center", fontSize: 45, fontWeight: 800, opacity: reveal(frame, 88) }}>Two actors. One shared booking state.</div>
          <div style={{ position: "absolute", top: 220, left: 60 }}>
            <ActorCard frame={frame} start={97} align="left" label="Human" action="Click claim slot" color={palette.cyan} />
          </div>
          <div style={{ position: "absolute", top: 220, right: 60 }}>
            <ActorCard frame={frame} start={104} align="right" label="Agent tool" action="claim_slot()" color={palette.lime} />
          </div>
          <RacePath frame={frame} />
          <div style={{ position: "absolute", top: 424, left: 0, right: 0, textAlign: "center", color: palette.red, fontFamily: "ui-monospace, monospace", fontWeight: 800, fontSize: 28, opacity: reveal(frame, 140) }}>
            both actions interleave at the same logical moment
          </div>
        </div>
      ) : null}

      {showFailure ? (
        <div style={{ opacity: sceneOpacity(172, 275), position: "absolute", inset: "160px 170px 92px", display: "flex", flexDirection: "column", alignItems: "center" }}>
          <div style={{ color: palette.red, fontSize: 29, fontWeight: 850, letterSpacing: 4, opacity: reveal(frame, 178) }}>INVARIANT FAILED</div>
          <div style={{ marginTop: 22, fontFamily: "ui-monospace, monospace", fontWeight: 800, fontSize: 46, opacity: reveal(frame, 186) }}>claims.active ≤ claims.capacity</div>
          <div style={{ marginTop: 48, display: "flex", gap: 20 }}>
            <div style={{ width: 270, padding: "30px 36px", borderRadius: 20, background: palette.panel, border: `1px solid ${palette.border}` }}>
              <div style={{ color: palette.muted, fontSize: 19, fontWeight: 700, letterSpacing: 2 }}>CAPACITY</div>
              <div style={{ marginTop: 10, fontFamily: "ui-monospace, monospace", fontSize: 72, color: palette.ink, fontWeight: 850 }}>1</div>
            </div>
            <div style={{ width: 270, padding: "30px 36px", borderRadius: 20, background: "#3a1723", border: `2px solid ${palette.red}`, boxShadow: `0 0 55px ${palette.red}33` }}>
              <div style={{ color: "#fecdd3", fontSize: 19, fontWeight: 700, letterSpacing: 2 }}>CLAIMED</div>
              <div style={{ marginTop: 10, fontFamily: "ui-monospace, monospace", fontSize: 72, color: "#fff1f2", fontWeight: 850 }}>2</div>
            </div>
          </div>
          <div style={{ marginTop: 44, color: palette.muted, fontSize: 29, opacity: reveal(frame, 224) }}>The failure is reduced to the smallest replayable repro.</div>
        </div>
      ) : null}

      {showReplay ? (
        <div style={{ opacity: reveal(frame, 264), position: "absolute", inset: "158px 145px 90px", display: "flex", flexDirection: "column", alignItems: "center" }}>
          <div style={{ fontSize: 51, fontWeight: 850, textAlign: "center" }}>Evidence, ready to replay.</div>
          <div style={{ marginTop: 40, width: 950, padding: "30px 36px", borderRadius: 20, background: "#050b14", border: `1px solid ${palette.border}` }}>
            <TerminalLine frame={frame} start={274} color={palette.lime}>✓ failure bundle saved</TerminalLine>
            <TerminalLine frame={frame} start={290} color={palette.muted}>.webmcp/runs/demo-race-failure/bundle.json</TerminalLine>
            <TerminalLine frame={frame} start={310} color={palette.cyan}><span style={{ color: palette.muted }}>$ </span>webmcp replay bundle.json --allow-mutations</TerminalLine>
            <TerminalLine frame={frame} start={330} color={palette.lime}>✓ same failure reproduced from portable evidence</TerminalLine>
          </div>
          <div style={{ marginTop: 34, color: palette.muted, fontSize: 26, opacity: reveal(frame, 336) }}>Run the included demo: <span style={{ color: palette.cyan, fontFamily: "ui-monospace, monospace" }}>webmcp demo-race</span></div>
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

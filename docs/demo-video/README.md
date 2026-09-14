# README demo video

This versioned Remotion composition creates the inline animated preview in
[`../assets/demo-race.gif`](../assets/demo-race.gif) and its linked MP4 source.

```bash
npm install
npx remotion render DemoRace ../assets/demo-race.mp4 --codec=h264
ffmpeg -y -i ../assets/demo-race.mp4 -vf "fps=10,scale=800:-2:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer" ../assets/demo-race.gif
```

The animation visualizes the real included `webmcp demo-race` workflow:
preflight, validation, a seeded adversarial race, invariant failure, reduction,
and replay. Keep wording, invariant names, and paths aligned with the CLI.

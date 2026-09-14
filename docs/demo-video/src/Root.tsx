import { Composition } from "remotion";
import { DemoRace } from "./Composition";
import "./index.css";

export const RemotionRoot: React.FC = () => (
  <Composition
    id="DemoRace"
    component={DemoRace}
    durationInFrames={390}
    fps={30}
    width={1280}
    height={720}
  />
);

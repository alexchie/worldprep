import React from "react";
import { Composition } from "remotion";
import { Captions, CaptionsProps } from "./Captions";

const FPS = 30;

export const Root: React.FC = () => (
  <Composition
    id="Captions"
    component={Captions}
    width={1080}
    height={1920}
    fps={FPS}
    durationInFrames={FPS}
    defaultProps={{ video: "base.mp4", duration: 1, cues: [], bottom: 400, lang: "zh" } as CaptionsProps}
    calculateMetadata={({ props }) => ({ durationInFrames: Math.max(1, Math.round(props.duration * FPS)) })}
  />
);

// Shorts 跳字幕：每句字幕彈出、文字逐字出現、數字用品牌金色強調；底下是已排好版的直式影片（含聲音與結尾定格）。
import React from "react";
import { AbsoluteFill, OffthreadVideo, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";

export type Cue = { start: number; end: number; text: string };
export type CaptionsProps = { video: string; duration: number; cues: Cue[]; bottom: number; lang: string };

const GOLD = "#d4a853";
const FONT = '"Noto Sans CJK TC", "Microsoft JhengHei", "Noto Sans", Arial, sans-serif';

const Line: React.FC<{ cue: Cue; lang: string }> = ({ cue, lang }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const local = frame - cue.start * fps;
  const pop = spring({ frame: local, fps, config: { damping: 12, stiffness: 180, mass: 0.6 } });
  const scale = interpolate(pop, [0, 1], [0.82, 1]);
  const lift = interpolate(pop, [0, 1], [40, 0]);
  // 文字在前 40% 的時間內逐字（英文逐字詞）出現
  const units = lang === "en" ? cue.text.split(/(\s+)/) : Array.from(cue.text);
  const reveal = Math.max(1, (cue.end - cue.start) * fps * 0.4);
  const shown = Math.ceil(interpolate(local, [0, reveal], [1, units.length], { extrapolateRight: "clamp", extrapolateLeft: "clamp" }));
  return (
    <div style={{ transform: `translateY(${lift}px) scale(${scale})`, opacity: pop, textAlign: "center", padding: "0 60px" }}>
      {units.map((u, i) => (
        <span key={i} style={{ opacity: i < shown ? 1 : 0, color: /\d/.test(u) ? GOLD : "#ffffff" }}>{u}</span>
      ))}
    </div>
  );
};

export const Captions: React.FC<CaptionsProps> = ({ video, cues, bottom, lang }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const cue = cues.find((c) => t >= c.start && t < c.end);
  return (
    <AbsoluteFill style={{ backgroundColor: "#0b1b3a" }}>
      <OffthreadVideo src={staticFile(video)} />
      <AbsoluteFill style={{ justifyContent: "flex-end", alignItems: "center", paddingBottom: bottom }}>
        {cue ? (
          <div style={{ fontFamily: FONT, fontWeight: 800, fontSize: lang === "en" ? 64 : 78, lineHeight: 1.25, color: "#fff",
                        WebkitTextStroke: "3px #06101f", paintOrder: "stroke fill", textShadow: "0 6px 18px rgba(0,0,0,0.55)" }}>
            <Line key={`${cue.start}`} cue={cue} lang={lang} />
          </div>
        ) : null}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

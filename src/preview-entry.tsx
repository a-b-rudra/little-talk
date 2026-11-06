import React, {useEffect, useRef, useState} from "react";
import {createRoot} from "react-dom/client";
import {Player, type PlayerRef} from "@remotion/player";
import {ChatComposition, type ChatCompositionProps} from "./ChatComposition";
import "./preview.css";

type PreviewController = {
  setData: (data: ChatCompositionProps | null) => void;
  playFromStart: () => void;
  pause: () => void;
  showEndFrame: () => void;
  getCurrentFrame: () => number;
  getDurationInFrames: () => number;
  getFps: () => number;
};

declare global {
  interface Window {
    chatPreviewController?: PreviewController;
  }
}

function PreviewPlayerHost(): React.ReactElement | null {
  const playerRef = useRef<PlayerRef>(null);
  const [data, setData] = useState<ChatCompositionProps | null>(null);
  const [revision, setRevision] = useState(0);
  const dataRef = useRef<ChatCompositionProps | null>(null);
  dataRef.current = data;

  useEffect(() => {
    window.chatPreviewController = {
      setData: (nextData) => {
        const previewData = nextData
          ? {...nextData, aspect_ratio: "preview", fps: 30}
          : null;
        dataRef.current = previewData;
        setData(previewData);
        setRevision((value) => value + 1);
      },
      playFromStart: () => {
        playerRef.current?.seekTo(0);
        playerRef.current?.play();
      },
      pause: () => playerRef.current?.pause(),
      showEndFrame: () => {
        const player = playerRef.current;
        if (player) {
          player.pause();
          const currentData = dataRef.current;
          if (currentData) player.seekTo(Math.ceil(currentData.duration * currentData.fps) - 1);
        }
      },
      getCurrentFrame: () => playerRef.current?.getCurrentFrame() ?? 0,
      getDurationInFrames: () => {
        const currentData = dataRef.current;
        return currentData ? Math.max(1, Math.ceil(currentData.duration * currentData.fps)) : 1;
      },
      getFps: () => dataRef.current?.fps ?? 30,
    };
    return () => {
      delete window.chatPreviewController;
    };
  }, []);

  if (!data) return null;
  const durationInFrames = Math.max(1, Math.ceil(data.duration * data.fps));
  return (
    <Player
      key={revision}
      ref={playerRef}
      component={ChatComposition}
      inputProps={data}
      durationInFrames={durationInFrames}
      compositionWidth={1080}
      compositionHeight={1620}
      fps={data.fps}
      initialFrame={durationInFrames - 1}
      moveToBeginningWhenEnded={false}
      controls={false}
      clickToPlay={false}
      doubleClickToFullscreen={false}
      style={{width: "100%", height: "100%"}}
    />
  );
}

const mount = document.querySelector("#remotion-player-root");
if (!mount) throw new Error("Missing #remotion-player-root mount point.");
createRoot(mount).render(<PreviewPlayerHost />);

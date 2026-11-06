import React from "react";
import {AbsoluteFill, Composition, useCurrentFrame, useVideoConfig} from "remotion";
import "./ChatComposition.css";

export type TimedMessage = {
  sender: string;
  text: string;
  timestamp: string;
  outgoing: boolean;
  typing_duration: number;
  reply_delay: number;
  typing_start: number;
  typing_end: number;
  reveal_at: number;
};

export type HistoryMessage = {
  sender: string;
  text: string;
  timestamp: string;
  outgoing: boolean;
};

export type ChatCompositionProps = {
  contact: string;
  avatar_display: string;
  start_time: string;
  duration: number;
  fps: number;
  aspect_ratio: string;
  history: HistoryMessage[];
  messages: TimedMessage[];
};

type Row =
  | {kind: "date"; key: string; timestamp: string; height: number}
  | {kind: "typing"; key: string; height: number}
  | {kind: "message"; key: string; message: HistoryMessage | TimedMessage; height: number; revealAt: number};

const COMPOSITION_WIDTH = 1080;
const PREVIEW_WIDTH = 540;
const PREVIEW_HEIGHT = 810;
const MESSAGE_GAP = 8;
const MESSAGE_FONT = 11;
const MESSAGE_LINE_HEIGHT = 14.85;
const TIME_FONT = 8;
const TIME_LINE_HEIGHT = 9.6;
const BUBBLE_PADDING_Y = 17;
const DATE_ROW_HEIGHT = 24;
const TYPING_ROW_HEIGHT = 28;
const SIGNIFICANT_GAP_MINUTES = 60;

const defaultProps: ChatCompositionProps = {
  contact: "Maya Chen",
  avatar_display: "MC",
  start_time: "09:41",
  duration: 3,
  fps: 30,
  aspect_ratio: "preview",
  history: [],
  messages: [],
};

const textContext = typeof document === "undefined"
  ? null
  : document.createElement("canvas").getContext("2d");

function hasSignificantGap(previous: string, current: string): boolean {
  const toMinutes = (time: string) => {
    const [hours, minutes] = time.split(":").map(Number);
    return hours * 60 + minutes;
  };
  return (toMinutes(current) - toMinutes(previous) + 1440) % 1440 >= SIGNIFICANT_GAP_MINUTES;
}

function wrappedLineCount(text: string, maxWidth: number): number {
  if (!textContext) return Math.max(1, Math.ceil(text.length / 30));
  textContext.font = `${MESSAGE_FONT}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
  let lineCount = 0;
  for (const paragraph of text.split("\n")) {
    const words = paragraph.split(/\s+/).filter(Boolean);
    if (words.length === 0) {
      lineCount += 1;
      continue;
    }
    let line = "";
    for (const word of words) {
      const candidate = line ? `${line} ${word}` : word;
      if (textContext.measureText(candidate).width <= maxWidth || !line) {
        line = candidate;
      } else {
        lineCount += 1;
        line = word;
      }
    }
    lineCount += 1;
  }
  return Math.max(1, lineCount);
}

function messageRowHeight(text: string, availableWidth: number): number {
  const maxBubbleWidth = availableWidth * 0.82;
  const textWidth = maxBubbleWidth - 22;
  const lines = wrappedLineCount(text, textWidth);
  return lines * MESSAGE_LINE_HEIGHT + BUBBLE_PADDING_Y + 4 + TIME_LINE_HEIGHT + MESSAGE_GAP;
}

function timeForFrame(messages: TimedMessage[], elapsed: number, startTime: string): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (elapsed >= messages[index].reveal_at) return messages[index].timestamp;
  }
  return startTime;
}

function statusIcons(): React.ReactElement {
  return (
    <svg className="remotion-status-icons" viewBox="0 0 76 16" aria-label="Cellular, Wi-Fi, and battery status">
      <path d="M1 14h3V11H1zm6 0h3V8H7zm6 0h3V5h-3zm6 0h3V2h-3z" />
      <path className="remotion-wifi-glyph" d="M30 5.1c5.5-4.8 13.5-4.8 19 0M33.3 8.4c3.6-3.1 9.8-3.1 13.4 0M37 11.4c1.6-1.4 5.4-1.4 7 0" />
      <circle cx="40.5" cy="13.2" r="1.35" />
      <rect x="57" y="3.5" width="17" height="9" rx="2" />
      <path d="M75 6.3v3.4" />
      <rect x="59" y="5.2" width="12" height="5.6" rx="1" />
    </svg>
  );
}

function VideoCallIcon(): React.ReactElement {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M14.5 7H5a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h9.5a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2Zm2 4.2 4-2.8v9.2l-4-2.8" />
    </svg>
  );
}

function TypingDots({frame}: {frame: number}): React.ReactElement {
  return (
    <div className="remotion-typing">
      {[0, 1, 2].map((index) => {
        const opacity = 0.45 + 0.5 * (0.5 + 0.5 * Math.sin(frame / 5 - index * 1.3));
        return <span key={index} style={{opacity}} />;
      })}
    </div>
  );
}

function MessageRow({
  row,
  frame,
  fps,
}: {
  row: Extract<Row, {kind: "message"}>;
  frame: number;
  fps: number;
}): React.ReactElement {
  const message = row.message;
  const progress = Math.min(1, Math.max(0, ((frame / fps) - row.revealAt) / 0.28));
  const scale = 0.88 + 0.12 * (1 - (1 - progress) ** 3);
  const opacity = Math.min(1, progress * 5);
  return (
    <div className={`remotion-message ${message.outgoing ? "is-outgoing" : "is-incoming"}`}>
      <div
        className={`remotion-bubble ${message.outgoing ? "is-outgoing" : "is-incoming"}`}
        style={{transform: `scale(${scale})`, opacity, transformOrigin: message.outgoing ? "bottom right" : "bottom left"}}
      >
        {message.text}
      </div>
      <time className="remotion-message-time">{message.timestamp}</time>
    </div>
  );
}

export function ChatComposition(props: ChatCompositionProps): React.ReactElement {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const elapsed = frame / fps;
  const scale = width / PREVIEW_WIDTH;
  const logicalHeight = height / scale;
  const previewCard = props.aspect_ratio === "preview";
  const cardInset = 10;
  const cardHeight = logicalHeight - cardInset * 2;
  let phoneHeight = previewCard ? cardHeight * 0.9 : logicalHeight * 0.94;
  let phoneWidth = phoneHeight * 390 / 844;
  if (phoneWidth > PREVIEW_WIDTH * 0.94) {
    phoneWidth = PREVIEW_WIDTH * 0.94;
    phoneHeight = phoneWidth * 844 / 390;
  }
  const phoneLeft = (PREVIEW_WIDTH - phoneWidth) / 2;
  const phoneTop = previewCard
    ? cardInset + (cardHeight - phoneHeight) * 0.08
    : (logicalHeight - phoneHeight) / 2;
  const phoneBorder = 5;
  const screenHeight = phoneHeight - phoneBorder * 2;
  const composerBaseHeight = 45;
  const homeHeight = 12;
  const headerHeight = 37 + 74 + 1;
  const outgoingMessage = props.messages.find(
    (message) => message.outgoing && message.typing_start <= elapsed && elapsed < message.reveal_at,
  );
  const outgoingText = outgoingMessage
    ? elapsed < outgoingMessage.typing_end && outgoingMessage.typing_duration > 0
      ? Array.from(outgoingMessage.text).slice(
          0,
          Math.round(
            outgoingMessage.text.length
            * Math.min(1, (elapsed - outgoingMessage.typing_start) / outgoingMessage.typing_duration),
          ),
        ).join("")
      : outgoingMessage.text
    : "";
  const composerLines = outgoingText
    ? wrappedLineCount(outgoingText, phoneWidth - 96)
    : 1;
  const composerHeight = Math.max(composerBaseHeight, Math.min(4, composerLines) * 15 + 15);
  const viewportHeight = screenHeight - headerHeight - composerHeight - homeHeight;
  const contentWidth = phoneWidth - phoneBorder * 2 - 20;
  const rows: Row[] = [];
  let previousHistoryTime: string | null = null;
  for (let index = 0; index < props.history.length; index += 1) {
    const message = props.history[index];
    if (previousHistoryTime && hasSignificantGap(previousHistoryTime, message.timestamp)) {
      rows.push({kind: "date", key: `history-gap-${index}`, timestamp: message.timestamp, height: DATE_ROW_HEIGHT + MESSAGE_GAP});
    }
    rows.push({
      kind: "message",
      key: `history-${index}`,
      message,
      height: messageRowHeight(message.text, contentWidth),
      revealAt: -1,
    });
    previousHistoryTime = message.timestamp;
  }
  rows.push({kind: "date", key: "start-time", timestamp: props.start_time, height: DATE_ROW_HEIGHT + MESSAGE_GAP});
  let previousTimestamp = props.start_time;
  for (let index = 0; index < props.messages.length; index += 1) {
    const message = props.messages[index];
    const hasGap = hasSignificantGap(previousTimestamp, message.timestamp);
    const typing = message.typing_duration > 0
      && message.typing_start <= elapsed
      && elapsed < message.typing_end;
    const revealed = elapsed >= message.reveal_at;
    if (typing && !message.outgoing) {
      if (hasGap) {
        rows.push({kind: "date", key: `message-gap-${index}`, timestamp: message.timestamp, height: DATE_ROW_HEIGHT + MESSAGE_GAP});
      }
      rows.push({kind: "typing", key: `typing-${index}`, height: TYPING_ROW_HEIGHT + MESSAGE_GAP});
    } else if (revealed) {
      if (hasGap) {
        rows.push({kind: "date", key: `message-gap-${index}`, timestamp: message.timestamp, height: DATE_ROW_HEIGHT + MESSAGE_GAP});
      }
      rows.push({
        kind: "message",
        key: `message-${index}`,
        message,
        height: messageRowHeight(message.text, contentWidth),
        revealAt: message.reveal_at,
      });
    }
    previousTimestamp = message.timestamp;
  }
  const listHeight = rows.reduce((total, row) => total + row.height, 0);
  const scrollOffset = Math.max(0, listHeight - Math.max(0, viewportHeight - 15));
  const cursorVisible = Math.floor(frame / Math.max(1, fps * 0.425)) % 2 === 0;
  const statusTime = timeForFrame(props.messages, elapsed, props.start_time);
  const displayName = props.contact.length > 23 ? `${props.contact.slice(0, 21)}…` : props.contact;

  return (
    <AbsoluteFill className="remotion-root">
      <div
        className="remotion-logical-canvas"
        style={{
          width: PREVIEW_WIDTH,
          height: logicalHeight,
          transform: `scale(${scale})`,
        }}
      >
        {previewCard ? <div className="remotion-stage-card" /> : null}
        <div
          className="remotion-phone"
          style={{left: phoneLeft, top: phoneTop, width: phoneWidth, height: phoneHeight}}
        >
          <div className="remotion-screen">
            <div className="remotion-status">
              <span>{statusTime}</span>
              {statusIcons()}
            </div>
            <div className="remotion-header">
              <div className="remotion-back"><span />12</div>
              <div className="remotion-contact">
                <div className={`remotion-avatar ${/\p{Extended_Pictographic}/u.test(props.avatar_display) ? "has-emoji" : ""}`}>
                  {props.avatar_display}
                </div>
                <div className="remotion-contact-name">{displayName}<i /></div>
              </div>
              <div className="remotion-video-call"><VideoCallIcon /></div>
            </div>
            <div className="remotion-divider" />
            <div className="remotion-chat-viewport" style={{height: viewportHeight}}>
              <div className="remotion-chat-list" style={{transform: `translateY(${-scrollOffset}px)`}}>
                {rows.map((row) => {
                  if (row.kind === "date") {
                    return <div key={row.key} className="remotion-date">Today · {row.timestamp}</div>;
                  }
                  if (row.kind === "typing") {
                    return <TypingDots key={row.key} frame={frame} />;
                  }
                  return <MessageRow key={row.key} row={row} frame={frame} fps={fps} />;
                })}
              </div>
            </div>
            <div className="remotion-composer" style={{height: composerHeight}}>
              <span className="remotion-plus">+</span>
              <div className={`remotion-composer-field ${outgoingText ? "is-typing" : ""}`}>
                <span>{outgoingText || "iMessage"}{outgoingText && cursorVisible ? <i className="remotion-cursor" /> : null}</span>
                {!outgoingText ? <i className="remotion-dictation">⌕</i> : null}
              </div>
              {outgoingText
                ? <span className="remotion-send">↑</span>
                : <svg className="remotion-mic" viewBox="0 0 20 24" aria-hidden="true"><rect x="7" y="2" width="6" height="13" rx="3" /><path d="M4.5 11.5a5.5 5.5 0 0 0 11 0M10 17v4m-3 0h6" /></svg>}
            </div>
            <div className="remotion-home-area"><div className="remotion-home-indicator" /></div>
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
}

export const ChatCompositionRoot: React.FC = () => (
  <Composition
    id="ChatPreview"
    component={ChatComposition}
    width={1080}
    height={1620}
    fps={30}
    durationInFrames={90}
    defaultProps={defaultProps}
    calculateMetadata={({props}) => ({
      width: props.aspect_ratio === "preview" ? 1080 : props.aspect_ratio === "iphone" ? 1170 : 1080,
      height: props.aspect_ratio === "preview" ? 1620 : props.aspect_ratio === "iphone" ? 2532 : props.aspect_ratio === "portrait" ? 1920 : 1080,
      fps: props.fps,
      durationInFrames: Math.max(1, Math.ceil(props.duration * props.fps)),
    })}
  />
);

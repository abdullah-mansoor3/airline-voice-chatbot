"use client";

import { useEffect, useRef, useState } from "react";

type ConnectionState = "connecting" | "connected" | "closed";

type ServerEvent =
  | { type: "ready" }
  | { type: "recording_started" }
  | { type: "processing"; stage: "stt" | "tts" }
  | {
      type: "transcript";
      text: string;
      language: string;
      detectedLanguage?: string;
    }
  | { type: "agent_response"; text: string; language: string }
  | { type: "tts_audio"; mimeType: string; bytes: number }
  | { type: "turn_complete" }
  | { type: "error"; message: string };

const wsUrl = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/voice";
const apiUrl = wsUrl.replace(/^ws/, "http").replace(/\/ws\/voice$/, "");

export default function Home() {
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [isRecording, setIsRecording] = useState(false);
  const [status, setStatus] = useState("Connecting");
  const [transcript, setTranscript] = useState("");
  const [detectedLanguage, setDetectedLanguage] = useState("");
  const [rawLanguage, setRawLanguage] = useState("");
  const [response, setResponse] = useState("");
  const [error, setError] = useState("");

  const wsRef = useRef<WebSocket | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const pendingChunksRef = useRef<Promise<void>[]>([]);

  useEffect(() => {
    const socket = new WebSocket(wsUrl);
    socket.binaryType = "blob";
    wsRef.current = socket;

    socket.onopen = () => {
      setConnection("connected");
      setStatus("Ready");
    };

    socket.onmessage = async (message) => {
      if (message.data instanceof Blob) {
        const audioUrl = URL.createObjectURL(message.data);
        if (audioRef.current) {
          audioRef.current.src = audioUrl;
          await audioRef.current.play().catch(() => undefined);
        }
        return;
      }

      const event = JSON.parse(message.data) as ServerEvent;
      handleServerEvent(event);
    };

    socket.onerror = () => {
      setError("WebSocket connection failed.");
      setStatus("Disconnected");
    };

    socket.onclose = () => {
      setConnection("closed");
      setStatus("Disconnected");
    };

    return () => {
      socket.close();
      stopStream();
    };
  }, []);

  function handleServerEvent(event: ServerEvent) {
    if (event.type === "ready") setStatus("Ready");
    if (event.type === "recording_started") setStatus("Listening");
    if (event.type === "processing") {
      setStatus(event.stage === "stt" ? "Transcribing" : "Speaking");
    }
    if (event.type === "transcript") {
      setTranscript(event.text);
      setDetectedLanguage(event.language);
      setRawLanguage(event.detectedLanguage ?? event.language);
    }
    if (event.type === "agent_response") setResponse(event.text);
    if (event.type === "turn_complete") setStatus("Ready");
    if (event.type === "error") {
      setError(event.message);
      setStatus("Ready");
    }
  }

  async function startRecording() {
    setError("");
    setTranscript("");
    setResponse("");
    setDetectedLanguage("");
    setRawLanguage("");

    const socket = wsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setError("WebSocket is not connected.");
      return;
    }

    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    streamRef.current = stream;

    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(
      stream,
      mimeType ? { mimeType } : undefined,
    );

    recorder.ondataavailable = async (event) => {
      if (event.data.size === 0 || socket.readyState !== WebSocket.OPEN) return;
      const pendingChunk = event.data.arrayBuffer().then((buffer) => {
        if (socket.readyState === WebSocket.OPEN) socket.send(buffer);
      });
      pendingChunksRef.current.push(pendingChunk);
      await pendingChunk;
    };

    recorder.onstop = async () => {
      await Promise.all(pendingChunksRef.current);
      pendingChunksRef.current = [];
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "stop" }));
      }
      stopStream();
    };

    socket.send(JSON.stringify({ type: "start", mimeType: recorder.mimeType }));
    recorder.start(500);
    recorderRef.current = recorder;
    setIsRecording(true);
  }

  function stopRecording() {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.stop();
    }
    setIsRecording(false);
  }

  function stopStream() {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }

  async function testUrduTts() {
    setError("");
    setStatus("Speaking");
    try {
      const result = await fetch(`${apiUrl}/voice/tts/test/urdu`);
      if (!result.ok) throw new Error(await result.text());

      const audioUrl = URL.createObjectURL(await result.blob());
      if (audioRef.current) {
        audioRef.current.src = audioUrl;
        await audioRef.current.play();
      }
      setStatus("Ready");
    } catch (err) {
      setStatus("Ready");
      setError(err instanceof Error ? err.message : "Urdu TTS test failed.");
    }
  }

  return (
    <main className="shell">
      <section className="console" aria-label="Voice dispute intake">
        <div className="topbar">
          <div>
            <p className="eyebrow">Airline dispute voice agent</p>
            <h1>Claim Intake</h1>
          </div>
          <span className={`status ${connection}`}>{status}</span>
        </div>

        <div className="controls">
          <button
            className="primary"
            disabled={connection !== "connected" || isRecording}
            onClick={startRecording}
            type="button"
          >
            Record
          </button>
          <button
            className="secondary"
            disabled={!isRecording}
            onClick={stopRecording}
            type="button"
          >
            Stop
          </button>
          <button
            className="secondary"
            disabled={isRecording}
            onClick={testUrduTts}
            type="button"
          >
            Test Urdu TTS
          </button>
        </div>

        {error ? <p className="error">{error}</p> : null}

        <div className="grid">
          <article className="panel">
            <div className="panelHeader">
              <h2>Transcript</h2>
              <span>
                {detectedLanguage || "-"}
                {rawLanguage && rawLanguage !== detectedLanguage
                  ? ` from ${rawLanguage}`
                  : ""}
              </span>
            </div>
            <p>{transcript || "..."}</p>
          </article>

          <article className="panel">
            <div className="panelHeader">
              <h2>Reply</h2>
              <span>Stub</span>
            </div>
            <p>{response || "..."}</p>
          </article>
        </div>

        <audio ref={audioRef} controls className="player" />
      </section>
    </main>
  );
}

function pickMimeType() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ];

  return candidates.find((candidate) => MediaRecorder.isTypeSupported(candidate));
}

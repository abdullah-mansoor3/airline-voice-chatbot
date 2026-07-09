"use client";

import type { Session } from "@supabase/supabase-js";
import { useCallback, useEffect, useRef, useState } from "react";
import { supabase } from "../lib/supabase";

// ── Types ────────────────────────────────────────────────────────────────────

type ConnectionState = "connecting" | "connected" | "closed";
type RecordingState = "idle" | "recording" | "processing";

type ServerEvent =
  | { type: "ready"; userId: string; conversationId: string }
  | { type: "auth_required"; message: string }
  | { type: "recording_started" }
  | { type: "processing"; stage: "stt" | "tts" }
  | {
      type: "transcript";
      text: string;
      englishText?: string | null;
      language: string;
      detectedLanguage?: string;
    }
  | { type: "agent_response"; text: string; language: string }
  | { type: "tts_audio"; mimeType: string; bytes: number }
  | { type: "turn_complete" }
  | { type: "cancelled" }
  | { type: "error"; message: string }
  | { type: "pong" };

type ConversationSummary = {
  id: string;
  title: string | null;
  status: string | null;
  primary_language: string | null;
  last_message_at: string | null;
};

type Message = {
  id: string;
  speaker: "user" | "agent";
  text: string;
  englishText?: string | null;
  language: string;
  timestamp: Date;
};

// ── Constants ────────────────────────────────────────────────────────────────

const wsUrl = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/voice";
const apiUrl = wsUrl.replace(/^ws/, "http").replace(/\/ws\/voice$/, "");

// VAD settings (must match backend VadSettings defaults)
const VAD_SILENCE_MS = 900;
const VAD_ENERGY_THRESHOLD = 0.015;
const VAD_BARGE_IN_THRESHOLD = 0.020;
const VAD_MIN_SPEECH_MS = 300;

// ── Main Component ───────────────────────────────────────────────────────────

export default function Home() {
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [recordingState, setRecordingState] = useState<RecordingState>("idle");
  const [status, setStatus] = useState("Connecting…");
  const [session, setSession] = useState<Session | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authMode, setAuthMode] = useState<"signin" | "signup">("signin");
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [currentTranscript, setCurrentTranscript] = useState("");
  const [currentResponse, setCurrentResponse] = useState("");
  const [detectedLanguage, setDetectedLanguage] = useState("");
  const [error, setError] = useState("");
  const [vadVolume, setVadVolume] = useState(0); // 0-1 for visualiser
  const [isPlayingTts, setIsPlayingTts] = useState(false);

  // Refs — stable across renders
  const wsRef = useRef<WebSocket | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const vadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const speechStartRef = useRef<number | null>(null);
  const silenceStartRef = useRef<number | null>(null);
  const vadRafRef = useRef<number | null>(null);
  const pendingChunksRef = useRef<Promise<void>[]>([]);
  const audioQueueRef = useRef<Blob[]>([]);
  const isPlayingRef = useRef(false);
  const nextExpectedBytesRef = useRef<number | null>(null);
  const messageFeedRef = useRef<HTMLDivElement | null>(null);

  // ── Auth ───────────────────────────────────────────────────────────────────

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setSession(data.session));
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
    });
    return () => subscription.unsubscribe();
  }, []);

  useEffect(() => {
    if (!session) {
      wsRef.current?.close();
      wsRef.current = null;
      setConnection("closed");
      setStatus("Login required");
      return;
    }
    loadConversations();
  }, [session]);

  // ── WebSocket ─────────────────────────────────────────────────────────────

  useEffect(() => {
    if (!session) return;

    const socket = new WebSocket(wsUrl);
    socket.binaryType = "blob";
    wsRef.current = socket;

    socket.onopen = () => {
      setConnection("connected");
      setStatus("Authenticating…");
      socket.send(
        JSON.stringify({
          type: "auth",
          accessToken: session.access_token,
          conversationId,
        }),
      );
    };

    socket.onmessage = async (message) => {
      if (message.data instanceof Blob) {
        // Phase 2: queue TTS audio blobs and play them sequentially.
        audioQueueRef.current.push(message.data);
        drainAudioQueue();
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
  }, [session, conversationId]);

  // ── Audio Queue (sequential TTS playback) ─────────────────────────────────

  function drainAudioQueue() {
    if (isPlayingRef.current || audioQueueRef.current.length === 0) return;
    const blob = audioQueueRef.current.shift()!;
    isPlayingRef.current = true;
    setIsPlayingTts(true);

    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.onended = () => {
      URL.revokeObjectURL(url);
      isPlayingRef.current = false;
      // Check for barge-in energy before playing the next sentence
      if (audioQueueRef.current.length > 0) {
        drainAudioQueue();
      } else {
        setIsPlayingTts(false);
      }
    };
    audio.onerror = () => {
      URL.revokeObjectURL(url);
      isPlayingRef.current = false;
      drainAudioQueue();
    };
    audio.play().catch(() => {
      isPlayingRef.current = false;
      drainAudioQueue();
    });
  }

  // ── Server Event Handler ───────────────────────────────────────────────────

  function handleServerEvent(event: ServerEvent) {
    switch (event.type) {
      case "ready":
        setStatus("Ready");
        setConversationId(event.conversationId);
        loadConversations();
        break;

      case "auth_required":
        setError(event.message);
        setStatus("Login required");
        break;

      case "recording_started":
        setStatus("Listening…");
        break;

      case "processing":
        setStatus(event.stage === "stt" ? "Transcribing…" : "Speaking…");
        setRecordingState("processing");
        break;

      case "transcript":
        setCurrentTranscript(event.text);
        setDetectedLanguage(event.language);
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            speaker: "user",
            text: event.text,
            englishText: event.englishText,
            language: event.language,
            timestamp: new Date(),
          },
        ]);
        break;

      case "agent_response":
        setCurrentResponse(event.text);
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            speaker: "agent",
            text: event.text,
            language: event.language,
            timestamp: new Date(),
          },
        ]);
        break;

      case "tts_audio":
        // The next WebSocket binary message is TTS audio of this size.
        nextExpectedBytesRef.current = event.bytes;
        break;

      case "turn_complete":
        setStatus("Ready");
        setRecordingState("idle");
        setIsPlayingTts(false);
        loadConversations();
        break;

      case "cancelled":
        // Barge-in acknowledged — clear audio queue.
        audioQueueRef.current = [];
        isPlayingRef.current = false;
        setIsPlayingTts(false);
        setStatus("Ready");
        setRecordingState("idle");
        break;

      case "error":
        setError(event.message);
        setStatus("Ready");
        setRecordingState("idle");
        break;
    }
  }

  // ── VAD (browser-side energy detection) ───────────────────────────────────

  function startVad(stream: MediaStream) {
    const ctx = new AudioContext();
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    audioCtxRef.current = ctx;
    analyserRef.current = analyser;

    const bufLen = analyser.frequencyBinCount;
    const dataArr = new Uint8Array(bufLen);
    let hasSpeech = false;

    function tick() {
      analyser.getByteFrequencyData(dataArr);
      const rms =
        Math.sqrt(
          dataArr.reduce((sum, v) => sum + v * v, 0) / bufLen,
        ) / 255;

      setVadVolume(Math.min(1, rms * 4));

      const isSpeech = rms >= VAD_ENERGY_THRESHOLD;

      if (isSpeech) {
        silenceStartRef.current = null;
        if (speechStartRef.current === null) {
          speechStartRef.current = Date.now();
        }
        hasSpeech = true;
      } else if (hasSpeech) {
        if (silenceStartRef.current === null) {
          silenceStartRef.current = Date.now();
        } else if (Date.now() - silenceStartRef.current >= VAD_SILENCE_MS) {
          const speechDuration =
            speechStartRef.current !== null
              ? Date.now() - speechStartRef.current
              : 0;
          if (speechDuration >= VAD_MIN_SPEECH_MS) {
            stopRecording();
            return;
          }
        }
      }

      // Barge-in detection: if TTS is playing and energy is high, cancel
      if (isPlayingRef.current && rms >= VAD_BARGE_IN_THRESHOLD) {
        sendCancel();
        stopStream();
        vadRafRef.current = requestAnimationFrame(tick);
        return;
      }

      vadRafRef.current = requestAnimationFrame(tick);
    }

    vadRafRef.current = requestAnimationFrame(tick);
  }

  function stopVad() {
    if (vadRafRef.current !== null) {
      cancelAnimationFrame(vadRafRef.current);
      vadRafRef.current = null;
    }
    analyserRef.current?.disconnect();
    audioCtxRef.current?.close().catch(() => undefined);
    audioCtxRef.current = null;
    analyserRef.current = null;
    setVadVolume(0);
    speechStartRef.current = null;
    silenceStartRef.current = null;
  }

  // ── Recording ─────────────────────────────────────────────────────────────

  async function startRecording() {
    setError("");
    setCurrentTranscript("");
    setCurrentResponse("");
    setDetectedLanguage("");

    const socket = wsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setError("WebSocket is not connected.");
      return;
    }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: 16000,
        },
      });
    } catch {
      setError("Microphone access was denied.");
      return;
    }
    streamRef.current = stream;

    const mimeType = pickMimeType();
    const recorder = new MediaRecorder(
      stream,
      mimeType ? { mimeType } : undefined,
    );

    recorder.ondataavailable = async (event) => {
      if (event.data.size === 0 || socket.readyState !== WebSocket.OPEN) return;
      const pending = event.data.arrayBuffer().then((buffer) => {
        if (socket.readyState === WebSocket.OPEN) socket.send(buffer);
      });
      pendingChunksRef.current.push(pending);
      await pending;
    };

    recorder.onstop = async () => {
      await Promise.all(pendingChunksRef.current);
      pendingChunksRef.current = [];
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "stop" }));
      }
      stopStream();
      stopVad();
    };

    socket.send(JSON.stringify({ type: "start", mimeType: recorder.mimeType }));
    recorder.start(250); // 250 ms chunks for lower latency
    recorderRef.current = recorder;
    setRecordingState("recording");

    startVad(stream);
  }

  function stopRecording() {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.stop();
    }
    setRecordingState("processing");
  }

  function stopStream() {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }

  function sendCancel() {
    const socket = wsRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "cancel" }));
    }
  }

  // ── Auth Actions ──────────────────────────────────────────────────────────

  async function submitAuth() {
    setError("");
    const action =
      authMode === "signin"
        ? supabase.auth.signInWithPassword({ email, password })
        : supabase.auth.signUp({ email, password });
    const { error: authError } = await action;
    if (authError) {
      setError(authError.message);
    } else {
      setStatus(authMode === "signin" ? "Signed in" : "Account created — check your email");
    }
  }

  async function signOut() {
    await supabase.auth.signOut();
    setConversationId(null);
    setConversations([]);
    setMessages([]);
    setCurrentTranscript("");
    setCurrentResponse("");
  }

  // ── Conversation History ──────────────────────────────────────────────────

  async function loadConversations() {
    const { data, error: loadError } = await supabase
      .from("conversations")
      .select("id,title,status,primary_language,last_message_at")
      .order("last_message_at", { ascending: false })
      .limit(20);

    if (loadError) {
      setError(loadError.message);
      return;
    }
    setConversations(data ?? []);
  }

  function selectConversation(id: string) {
    setConversationId(id);
    setMessages([]);
    setCurrentTranscript("");
    setCurrentResponse("");
  }

  function startNewConversation() {
    setConversationId(null);
    setMessages([]);
    setCurrentTranscript("");
    setCurrentResponse("");
    setDetectedLanguage("");
  }

  async function testUrduTts() {
    setError("");
    setStatus("Speaking…");
    try {
      const result = await fetch(`${apiUrl}/voice/tts/test/urdu`);
      if (!result.ok) throw new Error(await result.text());
      const blob = await result.blob();
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => {
        URL.revokeObjectURL(url);
        setStatus("Ready");
      };
      await audio.play();
    } catch (err) {
      setStatus("Ready");
      setError(err instanceof Error ? err.message : "Urdu TTS test failed.");
    }
  }

  // ── Auto-scroll message feed ──────────────────────────────────────────────

  useEffect(() => {
    messageFeedRef.current?.scrollTo({
      top: messageFeedRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  // ── Derived state ─────────────────────────────────────────────────────────

  const isConnected = connection === "connected";
  const isRecording = recordingState === "recording";
  const isProcessing = recordingState === "processing";
  const canRecord = !!session && isConnected && !isRecording && !isProcessing;

  const barWidth = `${Math.round(vadVolume * 100)}%`;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="shell">
      {/* ── Sidebar ── */}
      <aside className="sidebar">
        <div className="brandMark">
          <div className="brandIcon" aria-hidden="true">✈</div>
          <div>
            <p className="brandEyebrow">Airline Dispute</p>
            <p className="brandName">Voice Agent</p>
          </div>
        </div>

        {/* Status pill */}
        <div className={`statusPill ${connection}`}>
          <span className="statusDot" aria-hidden="true" />
          {status}
        </div>

        {/* Auth panel / account bar */}
        {!session ? (
          <form
            className="authForm"
            onSubmit={(e) => {
              e.preventDefault();
              submitAuth();
            }}
          >
            <h2 className="sidebarHeading">Sign in to continue</h2>
            <input
              id="auth-email"
              autoComplete="email"
              className="input"
              onChange={(e) => setEmail(e.target.value)}
              placeholder="Email"
              type="email"
              value={email}
            />
            <input
              id="auth-password"
              autoComplete="current-password"
              className="input"
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              type="password"
              value={password}
            />
            <button id="auth-submit" className="btnPrimary" type="submit">
              {authMode === "signin" ? "Sign In" : "Create Account"}
            </button>
            <button
              id="auth-toggle"
              className="btnGhost"
              onClick={() =>
                setAuthMode(authMode === "signin" ? "signup" : "signin")
              }
              type="button"
            >
              {authMode === "signin"
                ? "Don't have an account? Sign up"
                : "Already have an account? Sign in"}
            </button>
          </form>
        ) : (
          <div className="accountInfo">
            <p className="accountEmail">{session.user.email}</p>
            <button
              id="sign-out-btn"
              className="btnGhost"
              onClick={signOut}
              type="button"
            >
              Sign Out
            </button>
          </div>
        )}

        {/* Conversation history */}
        {session && conversations.length > 0 && (
          <section className="historySection">
            <h2 className="sidebarHeading">Conversations</h2>
            <button
              id="new-convo-btn"
              className="btnGhost newConvoBtn"
              onClick={startNewConversation}
              type="button"
            >
              + New conversation
            </button>
            <div className="historyList">
              {conversations.map((c) => (
                <button
                  key={c.id}
                  id={`convo-${c.id}`}
                  className={`historyItem ${c.id === conversationId ? "active" : ""}`}
                  onClick={() => selectConversation(c.id)}
                  type="button"
                >
                  <span className="historyTitle">
                    {c.title || "Voice claim"}
                  </span>
                  <span className="historyMeta">
                    {c.primary_language?.toUpperCase() ?? "—"} ·{" "}
                    {c.status ?? "active"}
                  </span>
                </button>
              ))}
            </div>
          </section>
        )}

        {/* Dev utilities */}
        {session && (
          <div className="devTools">
            <button
              id="test-tts-btn"
              className="btnGhost"
              disabled={isRecording}
              onClick={testUrduTts}
              type="button"
            >
              Test Urdu TTS
            </button>
          </div>
        )}
      </aside>

      {/* ── Main ── */}
      <main className="main">
        <header className="mainHeader">
          <div>
            <p className="eyebrow">Bilingual voice dispute resolution</p>
            <h1 className="mainTitle">Claim Intake</h1>
          </div>
          {detectedLanguage && (
            <span className="langBadge">
              {detectedLanguage === "ur" ? "🇵🇰 Urdu" : "🇬🇧 English"}
            </span>
          )}
        </header>

        {/* Error banner */}
        {error && (
          <div className="errorBanner" role="alert">
            <span className="errorIcon" aria-hidden="true">⚠</span>
            {error}
            <button
              className="errorDismiss"
              onClick={() => setError("")}
              type="button"
              aria-label="Dismiss error"
            >
              ✕
            </button>
          </div>
        )}

        {/* ── Message feed ── */}
        <div className="messageFeed" ref={messageFeedRef} aria-label="Conversation">
          {messages.length === 0 && (
            <div className="emptyFeed">
              <div className="emptyIcon" aria-hidden="true">🎙</div>
              <p>Press Record and state your airline dispute in Urdu, English, or both.</p>
              <p className="emptyHint">
                We support refund claims, cancellations, and delay compensation.
              </p>
            </div>
          )}
          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`bubble ${msg.speaker}`}
              dir={msg.language === "ur" ? "rtl" : "ltr"}
            >
              <div className="bubbleLabel">
                {msg.speaker === "user" ? "You" : "Agent"}
              </div>
              <div className="bubbleText">{msg.text}</div>
              {msg.englishText && msg.language !== "en" && (
                <div className="bubbleTranslation">
                  <span className="translationLabel">English:</span> {msg.englishText}
                </div>
              )}
            </div>
          ))}
          {isProcessing && (
            <div className="bubble agent processing">
              <div className="bubbleLabel">Agent</div>
              <div className="typingDots">
                <span /><span /><span />
              </div>
            </div>
          )}
        </div>

        {/* ── Voice controls ── */}
        <footer className="controls">
          {/* VAD volume visualiser */}
          <div className="vadBar" aria-hidden="true">
            <div className="vadFill" style={{ width: barWidth }} />
          </div>

          <div className="controlRow">
            {/* Main record / stop button */}
            {!isRecording ? (
              <button
                id="record-btn"
                className={`recBtn ${!canRecord ? "disabled" : ""}`}
                disabled={!canRecord}
                onClick={startRecording}
                type="button"
                aria-label="Start recording"
              >
                <span className="recIcon" aria-hidden="true">●</span>
                Record
              </button>
            ) : (
              <button
                id="stop-btn"
                className="recBtn recording"
                onClick={stopRecording}
                type="button"
                aria-label="Stop recording"
              >
                <span className="stopIcon" aria-hidden="true">■</span>
                Stop
              </button>
            )}

            {/* TTS playing indicator */}
            {isPlayingTts && (
              <span className="ttsIndicator" aria-live="polite">
                <span className="ttsWave" aria-hidden="true">▶</span>
                Speaking…
              </span>
            )}
          </div>

          <p className="controlHint">
            {!session
              ? "Sign in to start a claim."
              : !isConnected
                ? "Reconnecting…"
                : isRecording
                  ? "VAD active — silence will auto-stop. Speak naturally."
                  : isProcessing
                    ? "Processing your claim…"
                    : "Ready — press Record to speak."}
          </p>
        </footer>
      </main>
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function pickMimeType(): string | undefined {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ];
  return candidates.find((c) => MediaRecorder.isTypeSupported(c));
}

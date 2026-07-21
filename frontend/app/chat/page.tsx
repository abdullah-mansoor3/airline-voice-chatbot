"use client";
import type { Session } from "@supabase/supabase-js";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AdminDebugPanel } from "../../components/AdminDebugPanel";
import { supabase } from "../../lib/supabase";

// ── Types ────────────────────────────────────────────────────────────────────

type ConnectionState = "connecting" | "connected" | "closed";
type RecordingState = "idle" | "recording" | "processing";
type LanguageMode = "auto" | "en" | "ur";
type AgentState = "IDLE" | "AGENT_BUSY";
type ActionLock = "record" | "send-text" | "stop" | "mute" | "hands-free" | "release-buffer";
type CancelReason = "manual" | "barge-in";

type ServerEvent =
  | { type: "ready"; userId: string; conversationId: string | null }
  | { type: "conversation_created"; conversationId: string }
  | { type: "auth_required"; message: string }
  | { type: "recording_started" }
  | { type: "processing"; stage: string }
  | { type: "transcript"; text: string; englishText?: string | null; language: string; detectedLanguage?: string }
  | { type: "agent_response"; text: string; language: string; citations?: Citation[] }
  | { type: "agent_token"; text: string }
  | { type: "tts_audio"; mimeType: string; bytes: number; purpose?: "notice" | "response" }
  | { type: "tts_error"; message_en: string; message_ur: string }
  | { type: "turn_complete" }
  | { type: "voice_mode_end" }
  | { type: "cancelled" }
  | { type: "error"; message: string }
  | { type: "pong" }
  | { type: "debug_trace"; entry: Record<string, unknown> }
  | { type: "debug_bundle"; payload: Record<string, unknown> }
  | { type: "debug_memory"; memory: Record<string, unknown> }
  | { type: "planning_complete"; tools: any[]; category?: string }
  | { type: "tool_start"; tool: string }
  | { type: "tool_complete"; tool: string; result: any }
  | { type: "generation_start"; chunks_count?: number };

type ConversationSummary = {
  id: string;
  user_id?: string | null;
  title: string | null;
  status: string | null;
  primary_language: string | null;
  last_message_at: string | null;
};

type StoredMessage = {
  id: string;
  speaker: "user" | "agent";
  original_text: string | null;
  english_text: string | null;
  created_at: string | null;
};

type Message = {
  id: string;
  speaker: "user" | "agent";
  text: string;
  englishText?: string | null;
  language: string;
  citations?: Citation[];
  timestamp: Date;
  isStreaming?: boolean;
};

type Citation = {
  id: string;
  title?: string | null;
  heading?: string | null;
  originalText?: string | null;
  score?: number | null;
};

type QueuedAudio = Blob;

// ── Constants ────────────────────────────────────────────────────────────────

const wsUrl = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/voice";
const apiUrl = wsUrl.replace(/^ws/, "http").replace(/\/ws\/voice$/, "");

// VAD settings (must match backend VadSettings defaults)
const VAD_SILENCE_MS = 950;
const VAD_MIN_SPEECH_MS = 450;
const RMS_BUFFER_SIZE = 5;
const MIN_SILENT_FRAMES = 3;
const MIN_VAD_THRESHOLD = 0.015;
const MAX_VAD_THRESHOLD = 0.18;

// ── Main Component ───────────────────────────────────────────────────────────

export default function ChatPage() {
  const router = useRouter();
  const [sidebarExpanded, setSidebarExpanded] = useState(true);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [recordingState, setRecordingState] = useState<RecordingState>("idle");
  const [status, setStatus] = useState("Connecting…");
  const [session, setSession] = useState<Session | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [connectionKey, setConnectionKey] = useState(0);
  const [messages, setMessages] = useState<Message[]>([]);
  const [currentTranscript, setCurrentTranscript] = useState("");
  const [currentResponse, setCurrentResponse] = useState("");
  const [detectedLanguage, setDetectedLanguage] = useState("");
  const [textDraft, setTextDraft] = useState("");
  const [languageMode, setLanguageMode] = useState<LanguageMode>("auto");
  const [conversationMode, setConversationMode] = useState(false);
  const [vadSensitivity, setVadSensitivity] = useState(0.95);
  const [isCalibrating, setIsCalibrating] = useState(false);
  const [error, setError] = useState("");
  const [vadVolume, setVadVolume] = useState(0);
  const [isPlayingTts, setIsPlayingTts] = useState(false);
  const [agentThinking, setAgentThinking] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);
  const [debugTrace, setDebugTrace] = useState<Record<string, unknown>[]>([]);
  const [debugBundle, setDebugBundle] = useState<Record<string, unknown> | null>(null);
  const [debugMemory, setDebugMemory] = useState<Record<string, unknown> | null>(null);
  const [adminDbData, setAdminDbData] = useState<Record<string, unknown> | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const vadTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const speechStartRef = useRef<number | null>(null);
  const silenceStartRef = useRef<number | null>(null);
  const vadRafRef = useRef<number | null>(null);
  const pendingChunksRef = useRef<Promise<void>[]>([]);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const currentAudioUrlRef = useRef<string | null>(null);
  const lastTurnWasVoiceRef = useRef(false);
  const pendingAutoListenRef = useRef(false);
  const conversationModeRef = useRef(false);
  const recordingStateRef = useRef<RecordingState>("idle");
  const recordingStartedWhileBusyRef = useRef(false);
  const isProcessingRef = useRef(false);
  const isStreamingRef = useRef(false);
  const handsFreeStreamRef = useRef<MediaStream | null>(null);
  const lastSpokenLanguageRef = useRef("ur");
  const waitNoticeAtRef = useRef(0);
  const connectionRef = useRef<ConnectionState>("connecting");
  const sessionRef = useRef<Session | null>(null);
  const nextExpectedBytesRef = useRef<number | null>(null);
  const messageFeedRef = useRef<HTMLDivElement | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  const isAdminRef = useRef(false);
  const userIdRef = useRef<string | null>(null);
  const rmsBufferRef = useRef<number[]>([]);
  const consecutiveSilentFramesRef = useRef(0);
  const noiseFloorRef = useRef(0);
  const vadSensitivityRef = useRef(0.95);
  const vadWorkletNodeRef = useRef<AudioWorkletNode | null>(null);
  const vadSourceNodeRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const vadWorkletLoadedRef = useRef(false);
  const actionLockRef = useRef<ActionLock | null>(null);
  const cancelSentForTurnRef = useRef(false);
  const cancelReasonRef = useRef<CancelReason | null>(null);
  const suppressTtsUntilProcessingRef = useRef(false);

  // =======================================================================
  // VOICE AGENT STATE MACHINE VARIABLES
  // =======================================================================
  // agentStateRef: The primary source of truth for the 3-way gate.
  // It is only "IDLE" if the server turn is complete, no TTS is playing, and the queue is empty.
  const agentStateRef = useRef<AgentState>("IDLE");

  // isServerTurnCompleteRef: Tracks if the backend LLM is currently processing a request.
  const isServerTurnCompleteRef = useRef(true);

  // isPlayingRef: Tracks if the HTMLAudioElement is actively playing TTS audio.
  const isPlayingRef = useRef(false);

  // audioQueueRef: Holds pending TTS sentences from the current turn that haven't played yet.
  const audioQueueRef = useRef<Blob[]>([]);

  // =======================================================================
  // DEFERRED AUDIO BUFFERING (User barges in while agent is BUSY)
  // =======================================================================
  const hasDeferredVoiceRef = useRef(false);
  const deferredVoiceBlobRef = useRef<Blob | null>(null);
  const deferredChunksRef = useRef<Blob[]>([]);
  const deferredMimeRef = useRef<string>("audio/webm");

  const pendingTtsPurposeRef = useRef<"notice" | "response" | undefined>(undefined);
  const ttsPausedForUserSpeechRef = useRef(false);
  const deferredRecorderRef = useRef<MediaRecorder | null>(null);

  function withActionLock(action: ActionLock, fn: () => void | Promise<void>) {
    if (actionLockRef.current) return;
    actionLockRef.current = action;
    try {
      const result = fn();
      if (result instanceof Promise) {
        void result.finally(() => {
          if (actionLockRef.current === action) actionLockRef.current = null;
        });
        return;
      }
    } finally {
      if (actionLockRef.current === action) actionLockRef.current = null;
    }
  }

  function sendJsonFrame(payload: Record<string, unknown>) {
    const socket = wsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(payload));
    return true;
  }

  function sendCancelFrame(reason: CancelReason = "manual") {
    if (cancelSentForTurnRef.current) return false;
    if (!sendJsonFrame({ type: "cancel" })) return false;
    cancelSentForTurnRef.current = true;
    cancelReasonRef.current = reason;
    suppressTtsUntilProcessingRef.current = true;
    return true;
  }

  function beginLocalTurn(statusText: string) {
    cancelSentForTurnRef.current = false;
    cancelReasonRef.current = null;
    isServerTurnCompleteRef.current = false;
    setRecordingState("processing");
    isProcessingRef.current = true;
    setAgentThinking(true);
    setStatus(statusText);
    updateAgentState();
  }

  function clearDeferredVoiceBuffer() {
    hasDeferredVoiceRef.current = false;
    deferredVoiceBlobRef.current = null;
    deferredChunksRef.current = [];
  }

  function resetVoiceTurnUi(statusText = "Ready") {
    setRecordingState("idle");
    isProcessingRef.current = false;
    setAgentThinking(false);
    setStatus(statusText);
  }

  function abortActiveRecording() {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      try {
        recorder.ondataavailable = null;
        recorder.onstop = null;
        recorder.stop();
      } catch {}
    }
    recorderRef.current = null;
    const deferredRecorder = deferredRecorderRef.current;
    if (deferredRecorder && deferredRecorder.state !== "inactive") {
      try {
        deferredRecorder.ondataavailable = null;
        deferredRecorder.onstop = null;
        deferredRecorder.stop();
      } catch {}
    }
    deferredRecorderRef.current = null;
    pendingChunksRef.current = [];
    clearDeferredVoiceBuffer();
  }

  function resetSessionState() {
    wsRef.current?.close();
    wsRef.current = null;
    stopVad();
    releaseHandsFreeStream();
    stopStream();
    clearPlayback({ releaseDeferred: false });
    conversationIdRef.current = null;
    setConversationId(null);
    setConversations([]);
    setMessages([]);
    setCurrentTranscript("");
    setCurrentResponse("");
    setDetectedLanguage("");
    setDebugTrace([]);
    setDebugBundle(null);
    setDebugMemory(null);
    setAdminDbData(null);
    setIsAdmin(false);
    isAdminRef.current = false;
    setConnectionKey((key) => key + 1);
    clearDeferredVoiceBuffer();
    actionLockRef.current = null;
    cancelSentForTurnRef.current = false;
    cancelReasonRef.current = null;
    suppressTtsUntilProcessingRef.current = false;
    isServerTurnCompleteRef.current = true;
    updateAgentState();
    isStreamingRef.current = false; // Reset streaming
  }

  function updateAgentState({ releaseDeferred = true }: { releaseDeferred?: boolean } = {}) {
    // The Three-Way Gate: Agent is busy if ANY of these are true.
    const busy = !isServerTurnCompleteRef.current || isPlayingRef.current || audioQueueRef.current.length > 0;
    const newState = busy ? "AGENT_BUSY" : "IDLE";

    if (agentStateRef.current !== newState) {
      console.log(`[AgentState] Transition: ${agentStateRef.current} -> ${newState}`);
      console.log(`[AgentState] Debug -> isServerTurnComplete: ${isServerTurnCompleteRef.current}, isPlaying: ${isPlayingRef.current}, queueLength: ${audioQueueRef.current.length}`);
      agentStateRef.current = newState;

      if (newState === "IDLE" && releaseDeferred) {
        console.log(`[AgentState] Agent is now IDLE. Checking for buffered queries...`);
        releaseBufferedQuery();
      }
    }
  }

  function releaseBufferedQuery() {
    if (!hasDeferredVoiceRef.current || !deferredVoiceBlobRef.current) {
      console.log("[Buffer] No buffered query to release.");
      return;
    }

    const blob = deferredVoiceBlobRef.current;
    const mime = deferredMimeRef.current;

    if (actionLockRef.current) {
      console.log("[Buffer] Action lock active, leaving buffered query queued.");
      return;
    }

    const socket = wsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      console.error("[Buffer] Socket disconnected, dropping buffered query.");
      clearDeferredVoiceBuffer();
      return;
    }

    withActionLock("release-buffer", async () => {
      console.log("[Buffer] Releasing buffered query to backend...");
      clearDeferredVoiceBuffer();
      beginLocalTurn("Processing buffered request…");

      console.log("[Buffer] Sending JSON start frame and Audio Blob...");
      if (!sendJsonFrame({ type: "start", mimeType: mime, languageMode })) {
        isServerTurnCompleteRef.current = true;
        resetVoiceTurnUi();
        updateAgentState();
        return;
      }

      const buffer = await blob.arrayBuffer();
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(buffer);
        sendJsonFrame({ type: "stop" });
        console.log("[Buffer] Buffered query successfully transmitted.");
      } else {
        isServerTurnCompleteRef.current = true;
        resetVoiceTurnUi();
        updateAgentState();
      }
    });
  }

  // ── Auth ───────────────────────────────────────────────────────────────────

  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  useEffect(() => {
    conversationModeRef.current = conversationMode;
    if (conversationMode && connectionRef.current === "connected" && sessionRef.current) {
      void (async () => {
        const stream = await ensureHandsFreeStream();
        if (stream && !vadWorkletNodeRef.current) {
          startVad(stream);
        }
        if (recordingStateRef.current === "idle") {
          pendingAutoListenRef.current = true;
          maybeAutoListen();
        }
      })();
    }
    if (!conversationMode) {
      // ── Issue #4 fix: stop any active recording immediately ──
      if (recorderRef.current && recorderRef.current.state !== "inactive") {
        try {
          recorderRef.current.onstop = null; // Prevent onstop from completing the turn
          recorderRef.current.stop();
        } catch {}
        recorderRef.current = null;

        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          sendCancelFrame("manual");
        }
      }
      // Stop the deferred recorder too if it exists
      if (deferredRecorderRef.current && deferredRecorderRef.current.state !== "inactive") {
        try {
          deferredRecorderRef.current.onstop = null;
          deferredRecorderRef.current.stop();
        } catch {}
        deferredRecorderRef.current = null;
      }

      stopVad();
      releaseHandsFreeStream();
      // Clear ALL buffered/deferred audio when hands-free is turned off
      clearDeferredVoiceBuffer();
      pendingChunksRef.current = [];
      resetVoiceTurnUi();
      setStatus("Ready");
    }
  }, [conversationMode]);

  useEffect(() => {
    conversationIdRef.current = conversationId;
  }, [conversationId]);

  useEffect(() => {
    recordingStateRef.current = recordingState;
  }, [recordingState]);

  useEffect(() => {
    vadSensitivityRef.current = vadSensitivity;
  }, [vadSensitivity]);

  useEffect(() => {
    connectionRef.current = connection;
  }, [connection]);

  useEffect(() => {
    if (error) {
      const timer = setTimeout(() => setError(""), 3000);
      return () => clearTimeout(timer);
    }
  }, [error]);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      resetSessionState();
      setSession(data.session);
      userIdRef.current = data.session?.user?.id ?? null;
      if (!data.session) router.replace("/login");
    });
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      const nextUserId = nextSession?.user?.id ?? null;
      if (userIdRef.current !== nextUserId) {
        resetSessionState();
      }
      if (!nextSession) {
        userIdRef.current = null;
        router.replace("/login");
      } else {
        userIdRef.current = nextUserId;
      }
      setSession(nextSession);
      if (!nextSession) router.replace("/login");
    });
    return () => subscription.unsubscribe();
  }, [router]);

  useEffect(() => {
    if (!session) {
      wsRef.current?.close();
      wsRef.current = null;
      setConnection("closed");
      setStatus("Login required");
      // ── Issue #2 fix: clear stale conversation list when logged out ──
      setConversations([]);
      setMessages([]);
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
          conversationId: conversationIdRef.current,
        }),
      );
    };

    socket.onmessage = async (message) => {
      if (message.data instanceof Blob) {
        if (suppressTtsUntilProcessingRef.current || (cancelSentForTurnRef.current && isServerTurnCompleteRef.current)) {
          return;
        }
        // Phase 2: queue TTS audio blobs and play them sequentially.
        audioQueueRef.current.push(message.data);
        updateAgentState();
        drainAudioQueue();
        return;
      }

      const event = JSON.parse(message.data) as ServerEvent;
      handleServerEvent(event);
    };

    socket.onerror = () => {
      setError("WebSocket connection failed.");
      setStatus("Disconnected");
      clearPlayback({ releaseDeferred: false });
      clearDeferredVoiceBuffer();
      isServerTurnCompleteRef.current = true;
      cancelSentForTurnRef.current = false;
      cancelReasonRef.current = null;
      suppressTtsUntilProcessingRef.current = false;
      resetVoiceTurnUi("Disconnected");
      updateAgentState();
    };

    socket.onclose = () => {
      setConnection("closed");
      setStatus("Disconnected");
      clearPlayback({ releaseDeferred: false });
      clearDeferredVoiceBuffer();
      pendingChunksRef.current = [];
      isServerTurnCompleteRef.current = true;
      cancelSentForTurnRef.current = false;
      cancelReasonRef.current = null;
      suppressTtsUntilProcessingRef.current = false;
      resetVoiceTurnUi("Disconnected");
      updateAgentState();
    };

    return () => {
      socket.close();
      releaseHandsFreeStream();
      stopStream();
      clearPlayback({ releaseDeferred: false });
      clearDeferredVoiceBuffer();
    };
  }, [session, connectionKey]);

  useEffect(() => {
    if (!session || !conversationId) {
      setMessages([]);
      return;
    }
    if (isProcessingRef.current) return;
    loadMessages(conversationId);
  }, [session, conversationId]);

  // ── Audio Queue (sequential TTS playback) ─────────────────────────────────

  function drainAudioQueue() {
    if (isPlayingRef.current || audioQueueRef.current.length === 0) return;
    if (conversationModeRef.current && !vadWorkletNodeRef.current) {
      void ensureHandsFreeStream().then((stream) => {
        if (stream && !vadWorkletNodeRef.current) startVad(stream);
      });
    }
    const blob = audioQueueRef.current.shift()!;
    isPlayingRef.current = true;
    setIsPlayingTts(true);

    const url = URL.createObjectURL(blob);
    currentAudioUrlRef.current = url;
    const audio = new Audio(url);
    currentAudioRef.current = audio;

    const finishCurrentAudio = () => {
      if (currentAudioRef.current !== audio) return false;
      cleanupCurrentAudio();
      isPlayingRef.current = false;
      return true;
    };

    audio.onended = () => {
      if (ttsPausedForUserSpeechRef.current) return;
      if (!finishCurrentAudio()) return;
      updateAgentState();

      if (audioQueueRef.current.length > 0) {
        drainAudioQueue();
      } else {
        setIsPlayingTts(false);
        maybeAutoListen();
      }
    };

    audio.onerror = () => {
      if (!finishCurrentAudio()) return;
      setIsPlayingTts(false);
      updateAgentState();
      if (audioQueueRef.current.length > 0) drainAudioQueue();
    };

    audio.play().catch(() => {
      if (!finishCurrentAudio()) return;
      setIsPlayingTts(false);
      updateAgentState();
      if (audioQueueRef.current.length > 0) drainAudioQueue();
    });
  }

  function cleanupCurrentAudio() {
    if (currentAudioUrlRef.current) {
      URL.revokeObjectURL(currentAudioUrlRef.current);
    }
    currentAudioRef.current = null;
    currentAudioUrlRef.current = null;
  }

  function playPleaseWaitNotice() {
    sendCancelFrame("barge-in");
    clearPlayback();

    if ('speechSynthesis' in window) {
      const utterance = new SpeechSynthesisUtterance(
        lastSpokenLanguageRef.current === 'ur' ? '\u0628\u0631\u0627\u06c1 \u06a9\u0631\u0645 \u0627\u0646\u062a\u0638\u0627\u0631 \u06a9\u0631\u06cc\u06ba' : 'Please wait'
      );
      utterance.lang = lastSpokenLanguageRef.current === 'ur' ? 'ur-PK' : 'en-US';
      window.speechSynthesis.speak(utterance);
    }
  }

  function clearPlayback({ releaseDeferred = true }: { releaseDeferred?: boolean } = {}) {
    const audio = currentAudioRef.current;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
    }
    cleanupCurrentAudio();
    audioQueueRef.current = [];
    isPlayingRef.current = false;
    setIsPlayingTts(false);
    ttsPausedForUserSpeechRef.current = false;
    updateAgentState({ releaseDeferred });
  }

  function playTtsErrorMessages(messageEn: string, messageUr: string) {
    // Use browser's speech synthesis for error messages
    if ('speechSynthesis' in window) {
      const utteranceEn = new SpeechSynthesisUtterance(messageEn);
      utteranceEn.lang = 'en-US';
      utteranceEn.rate = 0.9;

      const utteranceUr = new SpeechSynthesisUtterance(messageUr);
      utteranceUr.lang = 'ur-PK';
      utteranceUr.rate = 0.9;

      // Play English first, then Urdu
      window.speechSynthesis.speak(utteranceEn);
      utteranceEn.onend = () => {
        window.speechSynthesis.speak(utteranceUr);
      };
    }
  }

  // ── Server Event Handler ───────────────────────────────────────────────────

  function handleServerEvent(event: ServerEvent) {
    if (
      cancelSentForTurnRef.current &&
      isServerTurnCompleteRef.current &&
      ["processing", "transcript", "agent_token", "agent_response", "tts_audio", "tts_error", "turn_complete"].includes(event.type)
    ) {
      return;
    }

    switch (event.type) {
      case "ready":
        setStatus("Ready");
        setConversationId(event.conversationId);
        loadConversations();
        fetch(`${apiUrl}/admin/debug/me`, {
          headers: { Authorization: `Bearer ${sessionRef.current?.access_token}` },
        })
          .then((response) => {
            if (response.ok) {
              return response.json().then((data) => {
                setIsAdmin(true);
                isAdminRef.current = true;
                setAdminDbData(data);
              });
            }
            setIsAdmin(false);
            isAdminRef.current = false;
            setAdminDbData(null);
            return null;
          })
          .catch(() => {
            setIsAdmin(false);
            isAdminRef.current = false;
          });
        break;

      case "conversation_created":
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
        suppressTtsUntilProcessingRef.current = false;
        isServerTurnCompleteRef.current = false;
        cancelSentForTurnRef.current = false;
        updateAgentState();
        isProcessingRef.current = true;
        setStatus(
          event.stage === "stt"
            ? "Transcribing…"
            : event.stage === "agent"
              ? "Generating…"
              : event.stage === "tts"
              ? "Speaking…"
              : event.stage.charAt(0).toUpperCase() + event.stage.slice(1) + "…"
        );
        setRecordingState("processing");
        if (event.stage !== "tts") {
          setAgentThinking(true);
        }
        break;

      case "planning_complete":
        console.log("Planning complete:", event.tools);
        setStatus("Planning complete");
        break;

      case "tool_start":
        console.log("Tool started:", event.tool);
        setStatus(`Running ${event.tool}...`);
        break;

      case "tool_complete":
        console.log("Tool complete:", event.tool, event.result);
        break;

      case "generation_start":
        console.log("Generation started");
        setStatus("Generating response...");
        break;

      case "transcript":
        lastSpokenLanguageRef.current = event.language;
        setCurrentTranscript(event.text);
        setDetectedLanguage(event.language);

        console.log("[Transcript] Received and showing immediately:", event.text);

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

      case "agent_token":
        isProcessingRef.current = false;
        setRecordingState("idle");
        setMessages((prev) => {
          const lastMsg = prev[prev.length - 1];
          if (lastMsg && lastMsg.speaker === "agent" && lastMsg.isStreaming) {
            return [
              ...prev.slice(0, -1),
              { ...lastMsg, text: lastMsg.text + event.text },
            ];
          }
          return [
            ...prev,
            {
              id: crypto.randomUUID(),
              speaker: "agent",
              text: event.text,
              language: lastSpokenLanguageRef.current || "ur",
              timestamp: new Date(),
              isStreaming: true,
            },
          ];
        });
        break;

      case "agent_response":
        isProcessingRef.current = false;
        setAgentThinking(false);
        setRecordingState("idle");
        setCurrentResponse(event.text);
        setMessages((prev) => {
          const lastMsg = prev[prev.length - 1];
          if (lastMsg && lastMsg.speaker === "agent" && lastMsg.isStreaming) {
            return [
              ...prev.slice(0, -1),
              {
                ...lastMsg,
                isStreaming: false,
                text: event.text,
                language: event.language,
                citations: event.citations,
              },
            ];
          }
          if (lastMsg && lastMsg.speaker === "agent" && lastMsg.text === event.text) {
            return prev;
          }
          return [
            ...prev,
            {
              id: crypto.randomUUID(),
              speaker: "agent",
              text: event.text,
              language: event.language,
              citations: event.citations,
              timestamp: new Date(),
            },
          ];
        });
        break;

      case "tts_audio":
        // The next WebSocket binary message is TTS audio of this size.
        nextExpectedBytesRef.current = event.bytes;
        updateAgentState();
        break;

      case "tts_error":
        // Play error messages without adding to conversation
        playTtsErrorMessages(event.message_en, event.message_ur);
        break;

      case "turn_complete":
        isServerTurnCompleteRef.current = true;
        const willReleaseOnComplete = hasDeferredVoiceRef.current && !!deferredVoiceBlobRef.current;
        updateAgentState();

        if (!willReleaseOnComplete) {
          isProcessingRef.current = false;
          setAgentThinking(false);
          setStatus("Ready");
          setRecordingState("idle");
        }

        if (conversationModeRef.current) {
          pendingAutoListenRef.current = true;
        }
        maybeAutoListen();
        loadConversations();
        break;

      case "voice_mode_end":
        pendingAutoListenRef.current = false;
        isProcessingRef.current = false;
        setAgentThinking(false);
        setConversationMode(false);
        releaseHandsFreeStream();
        setStatus("Voice mode ended");
        setRecordingState("idle");
        break;

      case "cancelled":
        if (!cancelSentForTurnRef.current && !isServerTurnCompleteRef.current) {
          break;
        }

        const cancelledForBargeIn = cancelReasonRef.current === "barge-in";
        const hadDeferredOnCancel = hasDeferredVoiceRef.current && !!deferredVoiceBlobRef.current;
        cancelSentForTurnRef.current = false;
        cancelReasonRef.current = null;
        if (!cancelledForBargeIn || !hadDeferredOnCancel) {
          suppressTtsUntilProcessingRef.current = false;
        }
        isServerTurnCompleteRef.current = true;
        if (!cancelledForBargeIn) {
          clearDeferredVoiceBuffer();
        }
        clearPlayback({ releaseDeferred: cancelledForBargeIn });
        if (!cancelledForBargeIn || !hadDeferredOnCancel) {
          resetVoiceTurnUi();
        }
        updateAgentState({ releaseDeferred: cancelledForBargeIn });

        if (conversationModeRef.current) {
          pendingAutoListenRef.current = true;
        }
        maybeAutoListen();
        break;

      case "error":
        clearDeferredVoiceBuffer();
        cancelSentForTurnRef.current = false;
        cancelReasonRef.current = null;
        suppressTtsUntilProcessingRef.current = false;
        isServerTurnCompleteRef.current = true;
        clearPlayback({ releaseDeferred: false });
        resetVoiceTurnUi();
        setError(event.message);
        updateAgentState({ releaseDeferred: false });
        if (conversationModeRef.current) {
          pendingAutoListenRef.current = true;
          maybeAutoListen();
        }
        break;

      case "debug_trace":
        setDebugTrace((prev) => {
          const chain = [...prev, event.entry];
          setDebugBundle((bundle) => ({
            ...(bundle ?? {}),
            reasoning_chain: chain,
          }));
          return chain;
        });
        break;

      case "debug_bundle":
        setDebugBundle(event.payload);
        if (Array.isArray(event.payload.reasoning_chain)) {
          setDebugTrace(event.payload.reasoning_chain as Record<string, unknown>[]);
        }
        if (event.payload.memory) {
          setDebugMemory(event.payload.memory as Record<string, unknown>);
        }
        break;

      case "debug_memory":
        setDebugMemory(event.memory);
        setDebugBundle((bundle) => ({
          ...(bundle ?? {}),
          memory: event.memory,
        }));
        break;
    }
  }

  function releaseHandsFreeStream() {
    stopVad();
    const stream = handsFreeStreamRef.current;
    stream?.getTracks().forEach((track) => track.stop());
    if (streamRef.current === stream) {
      streamRef.current = null;
    }
    handsFreeStreamRef.current = null;
  }

  async function calibrateNoiseFloor(stream: MediaStream) {
    setIsCalibrating(true);
    const ctx = new AudioContext();
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);

    const bufLen = analyser.frequencyBinCount;
    const dataArr = new Uint8Array(bufLen);
    const samples: number[] = [];

    for (let i = 0; i < 100; i++) {
      analyser.getByteFrequencyData(dataArr);
      const rms = Math.sqrt(dataArr.reduce((sum, v) => sum + v * v, 0) / bufLen) / 255;
      samples.push(rms);
      await new Promise(r => setTimeout(r, 20));
    }

    const avgNoise = samples.reduce((a, b) => a + b, 0) / samples.length;
    noiseFloorRef.current = avgNoise;
    setIsCalibrating(false);

    source.disconnect();
    await ctx.close();
  }

  async function ensureHandsFreeStream(): Promise<MediaStream | null> {
    if (!conversationModeRef.current) return null;
    if (handsFreeStreamRef.current?.active) {
      return handsFreeStreamRef.current;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: 16000,
        },
      });
      handsFreeStreamRef.current = stream;
      streamRef.current = stream;
      await calibrateNoiseFloor(stream);
      return stream;
    } catch {
      setError("Microphone access was denied.");
      return null;
    }
  }

  function sendSpeechDuringProcessing() {
    const socket = wsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    if (Date.now() - waitNoticeAtRef.current < 4000) return;
    waitNoticeAtRef.current = Date.now();
    socket.send(
      JSON.stringify({ type: "speech_during_processing", languageMode }),
    );
  }

  // ── VAD (AudioWorklet-based) ─────────────────────────────────────────────

  async function startVad(stream: MediaStream) {
    try {
      if (!audioCtxRef.current || audioCtxRef.current.state === "closed") {
        audioCtxRef.current = new AudioContext();
        vadWorkletLoadedRef.current = false;
      }
      if (audioCtxRef.current.state === "suspended") {
        await audioCtxRef.current.resume();
      }

      if (!vadWorkletLoadedRef.current) {
        await audioCtxRef.current.audioWorklet.addModule("/vad-processor.js");
        vadWorkletLoadedRef.current = true;
      }

      stopVadNodes();

      const source = audioCtxRef.current.createMediaStreamSource(stream);
      vadSourceNodeRef.current = source;

      const workletNode = new AudioWorkletNode(audioCtxRef.current, "vad-processor");
      vadWorkletNodeRef.current = workletNode;

      workletNode.port.onmessage = (event: MessageEvent<{ type: string; rms: number }>) => {
        const { type, rms } = event.data;
        if (type === "volume") {
          setVadVolume(Math.min(rms * 8, 1));
        } else if (type === "speech_start") {
          if (recordingStateRef.current !== "recording") {
            if (conversationModeRef.current) {
              void startRecording();
            } else if (!isPlayingRef.current && agentStateRef.current === "IDLE") {
              void startRecording();
            }
          }
        } else if (type === "speech_end") {
          if (recordingStateRef.current === "recording") {
            stopRecording();
          }
        } else if (type === "debug") {
          console.log('[VAD Debug]', event.data);
        }
      };

      source.connect(workletNode);
    } catch (error) {
      console.error("Failed to initialize VAD:", error);
      setError("Voice activity detection failed to initialize.");
    }
  }

  function stopVadNodes() {
    try { vadWorkletNodeRef.current?.disconnect(); } catch {}
    try { vadWorkletNodeRef.current?.port.close(); } catch {}
    vadWorkletNodeRef.current = null;
    try { vadSourceNodeRef.current?.disconnect(); } catch {}
    vadSourceNodeRef.current = null;
  }

  function stopVad() {
    stopVadNodes();
    setVadVolume(0);
    speechStartRef.current = null;
    silenceStartRef.current = null;
  }

  // ── Recording ─────────────────────────────────────────────────────────────

  async function startRecording() {
    if (actionLockRef.current || recordingStateRef.current !== "idle") return;
    actionLockRef.current = "record";
    setError("");
    setCurrentTranscript("");
    setCurrentResponse("");
    setDetectedLanguage("");
    lastTurnWasVoiceRef.current = true;

    const socket = wsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setError("WebSocket is not connected.");
      actionLockRef.current = null;
      return;
    }

    let stream: MediaStream | null = null;
    if (conversationModeRef.current && handsFreeStreamRef.current?.active) {
      stream = handsFreeStreamRef.current;
    } else {
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
        actionLockRef.current = null;
        return;
      }
      if (conversationModeRef.current) {
        handsFreeStreamRef.current = stream;
      }
    }
    streamRef.current = stream;

    const mimeType = pickMimeType();
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(
        stream,
        mimeType ? { mimeType } : undefined,
      );
    } catch {
      setError("This browser cannot record microphone audio.");
      stopStream();
      actionLockRef.current = null;
      return;
    }

    const isBusy = agentStateRef.current === "AGENT_BUSY";
    recordingStartedWhileBusyRef.current = isBusy;

    // Always record the MIME so onstop can build a valid Blob regardless of path.
    deferredMimeRef.current = recorder.mimeType || "audio/webm";

    if (isBusy) {
      deferredChunksRef.current = [];
    } else {
      pendingChunksRef.current = [];
      sendJsonFrame({
        type: "start",
        mimeType: recorder.mimeType,
        languageMode,
      });
      cancelSentForTurnRef.current = false;
      isServerTurnCompleteRef.current = false;
      updateAgentState();
    }

    recorder.ondataavailable = async (event) => {
      if (event.data.size === 0) return;

      // isBusy is intentionally a stale closure captured at recording start.
      // updateAgentState() is called right after we send "start", which sets
      // agentStateRef to AGENT_BUSY. Reading it live here would make every
      // chunk look busy and route everything to deferredChunksRef, silently
      // killing the turn. The closure correctly reflects whether the agent
      // was busy BEFORE this turn began.
      if (isBusy) {
        deferredChunksRef.current.push(event.data);
        return;
      }

      if (socket.readyState !== WebSocket.OPEN) return;
      const pending = event.data.arrayBuffer().then((buffer) => {
        if (socket.readyState === WebSocket.OPEN) socket.send(buffer);
      });
      pendingChunksRef.current.push(pending);
      await pending;
    };

    recorder.onstop = async () => {
      const currentlyBusy = agentStateRef.current === "AGENT_BUSY";
      const startedWhileBusy = recordingStartedWhileBusyRef.current;

      if (deferredChunksRef.current.length > 0) {
        const blob = new Blob(deferredChunksRef.current, { type: deferredMimeRef.current });
        deferredVoiceBlobRef.current = blob;
        hasDeferredVoiceRef.current = true;
        deferredChunksRef.current = [];

        if (currentlyBusy) {
          playPleaseWaitNotice();
        } else if (startedWhileBusy) {
          releaseBufferedQuery();
        }
      } else {
        await Promise.all(pendingChunksRef.current);
        pendingChunksRef.current = [];
        if (socket.readyState === WebSocket.OPEN) {
          sendJsonFrame({ type: "stop" });
        }
      }

      recorderRef.current = null;
      // Read agent state live — isBusy closure was captured at recording start and may be stale.
      if (!startedWhileBusy) {
        isProcessingRef.current = true;
      }
      // When agent is busy, isProcessingRef stays as-is — the server turn is still
      // in flight so thinking-dots / stop-button must remain visible.
      stopVad();
      if (!conversationModeRef.current) stopStream();
    };

    recorder.start(250);
    recorderRef.current = recorder;
    setRecordingState("recording");
    setStatus("Listening…");
    if (actionLockRef.current === "record") actionLockRef.current = null;

    if (!vadWorkletNodeRef.current) {
      void startVad(stream);
    }
  }

  function stopRecording() {
    if (actionLockRef.current === "stop" || actionLockRef.current === "mute") return;
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      try {
        recorder.requestData();
      } catch {
        // Some browsers throw if a final chunk is already queued.
      }
      recorder.stop();
    }
    stopVad();
    speechStartRef.current = null;
    silenceStartRef.current = null;
    const startedWhileBusy = recordingStartedWhileBusyRef.current;
    if (!startedWhileBusy) {
      setRecordingState("processing");
      isProcessingRef.current = true;
      setAgentThinking(true);
      setStatus("Transcribing…");
    } else {
      // ── Issue #5 fix: agent is still thinking/streaming – only reset the
      // recording state (so mic shows as idle) but leave isProcessingRef
      // alone so the thinking-dots and stop-generation button stay visible.
      setRecordingState("idle");
      // isProcessingRef stays as-is – the server turn is still in flight
    }
  }

  function stopStream() {
    if (conversationModeRef.current && handsFreeStreamRef.current?.active) {
      return;
    }
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }

  function sendCancel() {
    stopAgentGeneration();
  }

  function stopAgentGeneration() {
    withActionLock("stop", () => {
      sendCancelFrame("manual");
      abortActiveRecording();
      isServerTurnCompleteRef.current = true;
      clearPlayback({ releaseDeferred: false });
      stopVad();
      resetVoiceTurnUi();
      updateAgentState({ releaseDeferred: false });

      if (conversationModeRef.current) {
        pendingAutoListenRef.current = true;
        maybeAutoListen();
      } else {
        pendingAutoListenRef.current = false;
      }
    });
  }

  function muteAssistantVoice() {
    withActionLock("mute", () => {
      sendCancelFrame("manual");
      abortActiveRecording();
      isServerTurnCompleteRef.current = true;
      clearPlayback({ releaseDeferred: false });
      resetVoiceTurnUi();
      updateAgentState({ releaseDeferred: false });

      if (conversationModeRef.current) {
        pendingAutoListenRef.current = true;
        maybeAutoListen();
      }
    });
  }

  function interruptAssistantAndListen() {
    sendCancelFrame("manual");
    clearDeferredVoiceBuffer();
    isServerTurnCompleteRef.current = true;
    clearPlayback({ releaseDeferred: false });
    stopVad();
    pendingAutoListenRef.current = false;
    setRecordingState("idle");
    isProcessingRef.current = false;
    setStatus("Listening…");
    if (conversationModeRef.current && recordingStateRef.current === "idle") {
      maybeAutoListen();
    }
  }

  function sendTextMessage() {
    withActionLock("send-text", () => {
      const text = textDraft.trim();
      if (!text) return;
      if (agentStateRef.current !== "IDLE" || recordingStateRef.current !== "idle") {
        setError("Please wait for the current turn to finish.");
        return;
      }
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        setError("WebSocket is not connected.");
        return;
      }
      setError("");
      setCurrentTranscript(text);
      setCurrentResponse("");
      setTextDraft("");
      setDebugTrace([]);
      setDebugBundle(null);
      lastTurnWasVoiceRef.current = false;

      if (!sendJsonFrame({ type: "text_message", text, languageMode })) {
        setError("WebSocket is not connected.");
        return;
      }
      beginLocalTurn("Generating…");
    });
  }

  function maybeAutoListen() {
    if (!pendingAutoListenRef.current || !conversationModeRef.current) return;
    if (
      !sessionRef.current ||
      connectionRef.current !== "connected" ||
      recordingStateRef.current !== "idle" ||
      agentStateRef.current !== "IDLE" ||
      isPlayingRef.current
    ) {
      return;
    }
    pendingAutoListenRef.current = false;
    void startRecording();
  }

  // ── Auth Actions ──────────────────────────────────────────────────────────

  async function signOut() {
    await supabase.auth.signOut();
    resetSessionState();
    router.replace("/login");
  }

  // ── Conversation History ──────────────────────────────────────────────────

  async function loadConversations() {
    if (!sessionRef.current?.user?.id) return;
    const { data, error: loadError } = await supabase
      .from("conversations")
      .select("id,user_id,title,status,primary_language,last_message_at")
      .eq("user_id", sessionRef.current.user.id)
      .order("last_message_at", { ascending: false })
      .limit(20);

    if (loadError) {
      setError(loadError.message);
      return;
    }
    setConversations(data ?? []);
  }

  async function deleteConversation(id: string) {
    setError("");
    if (!session) return;

    // ── Issue #1 fix: optimistic removal – remove from UI immediately,
    // only restore if the backend returns a real error (not 404).
    const convoToRestore = conversations.find((c) => c.id === id);
    setConversations((prev) => prev.filter((c) => c.id !== id));

    if (conversationId === id) {
      setConversationId(null);
      setConnectionKey((k) => k + 1);
      setMessages([]);
      setCurrentTranscript("");
      setCurrentResponse("");
    }

    const response = await fetch(`${apiUrl}/conversations/${id}`, {
      method: "DELETE",
      headers: {
        Authorization: `Bearer ${session.access_token}`,
      },
    });

    if (!response.ok && response.status !== 404) {
      // Real failure – restore the item in the UI
      const message = await response.text();
      setError(message || "Could not delete conversation.");
      if (convoToRestore) {
        setConversations((prev) => {
          const restored = [convoToRestore, ...prev];
          restored.sort((a, b) => new Date(b.last_message_at || 0).getTime() - new Date(a.last_message_at || 0).getTime());
          return restored;
        });
      }
    }
    // On success or 404 we intentionally do NOT call loadConversations()
    // so rapid-delete clicks don't cause items to reappear.
  }

  async function loadMessages(id: string) {
    if (!sessionRef.current?.user?.id) return;
    const ownerCheck = await supabase
      .from("conversations")
      .select("id,user_id")
      .eq("id", id)
      .eq("user_id", sessionRef.current.user.id)
      .maybeSingle();
    if (ownerCheck.error || !ownerCheck.data) {
      setMessages([]);
      setConversationId(null);
      setConnectionKey((key) => key + 1);
      return;
    }
    const { data, error: loadError } = await supabase
      .from("messages")
      .select("id,speaker,original_text,english_text,created_at")
      .eq("conversation_id", id)
      .order("turn_index", { ascending: true });

    if (loadError) {
      setError(loadError.message);
      setMessages([]);
      return;
    }

    setMessages(
      ((data ?? []) as StoredMessage[]).map((message) => {
        const text = message.original_text ?? "";
        return {
          id: message.id,
          speaker: message.speaker,
          text,
          englishText: message.english_text,
          language: detectTextLanguage(text),
          timestamp: message.created_at ? new Date(message.created_at) : new Date(),
        };
      }),
    );
  }

  function selectConversation(id: string) {
    if (conversationId === id) return;
    setMessages([]);
    setConversationId(id);
    setConnectionKey((k) => k + 1);
    setCurrentTranscript("");
    setCurrentResponse("");
  }

  function startNewConversation() {
    if (conversationId === null) return;
    setConversationId(null);
    setConnectionKey((k) => k + 1);
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
    const feed = messageFeedRef.current;
    if (!feed) return;
    requestAnimationFrame(() => {
      feed.scrollTo({
        top: feed.scrollHeight,
        behavior: messages.length > 8 ? "auto" : "smooth",
      });
    });
  }, [messages]);

  // ── Derived state ─────────────────────────────────────────────────────────

  const isConnected = connection === "connected";
  const isRecording = recordingState === "recording";
  const isProcessing = recordingState === "processing";
  const hasStreamingAgent = messages.some(
    (msg) => msg.speaker === "agent" && msg.isStreaming,
  );
  const showProcessingBubble = agentThinking && !hasStreamingAgent;
  const canRecord = !!session && isConnected && !isRecording && !isProcessing && !agentThinking && !isPlayingTts;
  const canSendText = !!session && isConnected && !isRecording && !isProcessing && !agentThinking && !isPlayingTts;

  const barWidth = `${Math.round(vadVolume * 100)}%`;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className={`shell ${sidebarExpanded ? "sidebar-open" : "sidebar-collapsed"}`}>
      {/* Sidebar */}
      <aside className={`sidebar ${sidebarExpanded ? "expanded" : "collapsed"}`}>
        <div className="sidebarTop">
          <Link href="/" className="brandMark" style={{ textDecoration: 'none' }}>
            <div className="brandIcon" aria-hidden="true" />
            {sidebarExpanded ? (
              <div>
                <p className="brandEyebrow">Airline Assistant</p>
                <p className="brandName">Claim Desk</p>
              </div>
            ) : null}
          </Link>
          <button
            className="sidebarToggle"
            onClick={() => setSidebarExpanded((value) => !value)}
            type="button"
            aria-label={sidebarExpanded ? "Collapse sidebar" : "Expand sidebar"}
          >
            {sidebarExpanded ? "‹" : "›"}
          </button>
        </div>

        {/* Status pill */}
        {sidebarExpanded ? (
          <div className={`statusPill ${connection}`}>
            <span className="statusDot" aria-hidden="true" />
            {status}
          </div>
        ) : null}

        {/* Conversation history */}
        {session && conversations.length > 0 && sidebarExpanded ? (
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
                <div
                  key={c.id}
                  id={`convo-${c.id}`}
                  className={`historyItem ${c.id === conversationId ? "active" : ""}`}
                  onClick={() => selectConversation(c.id)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") selectConversation(c.id);
                  }}
                >
                  <div className="historyText">
                    <span className="historyTitle">
                      {c.title || "Voice claim"}
                    </span>
                    <span className="historyMeta">
                      {c.primary_language?.toUpperCase() ?? "—"} ·{" "}
                      {c.status ?? "active"}
                    </span>
                  </div>
                  <button
                    className="historyDelete"
                    onClick={(event) => {
                      event.stopPropagation();
                      deleteConversation(c.id);
                    }}
                    type="button"
                    aria-label={`Delete ${c.title || "conversation"}`}
                  >
                    Delete
                  </button>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {isAdmin && sidebarExpanded ? (
          <AdminDebugPanel
            adminDbData={adminDbData}
            debugBundle={debugBundle}
            debugMemory={debugMemory}
            debugTrace={debugTrace}
            onClear={() => {
              setDebugTrace([]);
              setDebugBundle(null);
            }}
          />
        ) : null}

        {session && sidebarExpanded ? (
          <details className="advancedPanel">
            <summary>
              <span className="controlIcon slidersIcon" aria-hidden="true" />
              Advanced
            </summary>
            <div className="advancedBody">
              <label className="modeField stacked">
                Language
                <select
                  value={languageMode}
                  onChange={(event) => setLanguageMode(event.target.value as LanguageMode)}
                >
                  <option value="auto">Auto</option>
                  <option value="ur">Force Urdu</option>
                  <option value="en">Force English</option>
                </select>
              </label>
            </div>
          </details>
        ) : null}

      </aside>

      <main className="main">
        <header className="mainHeader">
          <div>
            <p className="eyebrow">Conversation</p>
            <h1 className="mainTitle">Airline Claim Assistant</h1>
          </div>
          <div className="mainHeaderRight">
            {detectedLanguage ? (
              <span className="langBadge">{detectedLanguage.toUpperCase()}</span>
            ) : null}
            {session ? (
              <nav className="topNav" aria-label="Account">
                <Link className="topNavLink" href="/">
                  Home
                </Link>
                <span className="topNavEmail">{session.user.email}</span>
                <button className="topNavButton" onClick={signOut} type="button">
                  Sign out
                </button>
              </nav>
            ) : null}
          </div>
        </header>

        {error && (
          <div className="errorBanner" role="alert">
            <span className="errorIcon" aria-hidden="true">!</span>
            {error}
            <button
              className="errorDismiss"
              onClick={() => setError("")}
              type="button"
              aria-label="Dismiss error"
            >
              Close
            </button>
          </div>
        )}

        {/* ── Message feed ── */}
        <div className="messageFeed" ref={messageFeedRef} aria-label="Conversation">
          {messages.length === 0 && (
            <div className="emptyFeed">
              <p className="emptyTitle">Start your claim</p>
              <p>Type your question or use the microphone. Urdu and English are supported.</p>
              <p className="emptyHint">
                Refunds, baggage rules, cancellations, and flight search.
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
              <div className="bubbleText">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {msg.text}
                </ReactMarkdown>
              </div>

              {msg.speaker === "user" && msg.language === "ur" && msg.englishText ? (
                <div className="bubbleTranslation" dir="ltr">
                  <span className="translationLabel">English: </span>
                  {msg.englishText}
                </div>
              ) : null}

              {msg.speaker === "agent" && msg.citations?.length ? (
                <div className="citations" dir="ltr">
                  <div className="citationTitle">Retrieved clauses</div>
                  {msg.citations.map((citation) => (
                    <details className="citationItem" key={citation.id}>
                      <summary>
                        {citation.title || "Policy document"}
                        {citation.heading ? ` · ${citation.heading}` : ""}
                      </summary>
                      <pre>{citation.originalText || "No original text returned."}</pre>
                    </details>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
          {showProcessingBubble && (
            <div className="bubble agent processing">
              <div className="bubbleLabel">Agent</div>
              <div className="typingDots">
                <span /><span /><span />
              </div>
            </div>
          )}
        </div>

        {/* ── Chat composer ── */}
        <footer className="controls">
          <div className="vadBar" aria-hidden="true">
            <div className="vadFill" style={{ width: barWidth }} />
          </div>

          <div className="modeRow compactModeRow">
            <button
              className={`voiceToggle ${conversationMode ? "active" : ""}`}
              onClick={() => withActionLock("hands-free", () => setConversationMode((value) => !value))}
              type="button"
              aria-pressed={conversationMode}
            >
              <span className="controlIcon voiceIcon" aria-hidden="true" />
              <span className="voiceToggleTrack">
                <span className="voiceToggleThumb" />
              </span>
              Hands-free
            </button>
            <span className="advancedHint">Audio settings are in Advanced.</span>
          </div>

          <div className="composerRow">
            <textarea
              className="composerInput"
              disabled={!canSendText}
              onChange={(event) => setTextDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  sendTextMessage();
                }
              }}
              placeholder="Type a claim, or use the mic"
              rows={1}
              value={textDraft}
            />

            {!isRecording && !isProcessing && !isPlayingTts && !agentThinking ? (
              <button
                className="sendBtn"
                disabled={!canSendText || !textDraft.trim()}
                onClick={sendTextMessage}
                type="button"
                aria-label="Send text"
              >
                Send
              </button>
            ) : null}

            {isRecording && (
              <button
                className="micBtn sendVoiceBtn"
                onClick={stopRecording}
                type="button"
                aria-label="Stop recording and send"
              >
                <span className="controlIcon sendVoiceIcon" aria-hidden="true" />
                Send voice
              </button>
            )}

            {agentThinking && (
              <button
                className="stopGenerationBtn iconStopBtn"
                onClick={stopAgentGeneration}
                type="button"
                aria-label="Stop generating response"
              >
                <span className="controlIcon stopIcon" aria-hidden="true" />
                Stop generating
              </button>
            )}

            {isPlayingTts && (
              <button
                className="muteVoiceBtn iconStopBtn"
                onClick={muteAssistantVoice}
                type="button"
                aria-label="Mute assistant voice"
              >
                <span className="controlIcon muteIcon" aria-hidden="true" />
                Mute voice
              </button>
            )}

            {!isRecording && !agentThinking && !isPlayingTts && (
              <button
                id="record-btn"
                className={`micBtn ${!canRecord ? "disabled" : ""}`}
                disabled={!canRecord}
                onClick={startRecording}
                type="button"
                aria-label="Start recording"
              >
                <span className="controlIcon micIcon" aria-hidden="true" />
                Mic
              </button>
            )}
          </div>

          <p className="controlHint">
            {!session
              ? "Redirecting to sign in…"
              : !isConnected
                ? "Reconnecting…"
                : conversationMode && isRecording
                  ? "Listening — pause briefly to send."
                  : conversationMode && isProcessing
                    ? "Processing your request…"
                    : conversationMode && isPlayingTts
                      ? "Speaking — you can interrupt."
                      : conversationMode
                        ? "Hands-free mode is on."
                        : isRecording
                          ? "Recording — tap Stop when finished."
                          : isProcessing
                            ? "Working on your request…"
                            : isPlayingTts
                              ? "Playing response…"
                              : "Type a message or tap Mic."}
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

function detectTextLanguage(text: string): "ur" | "en" {
  if ([...text].some((char) => char >= "\u0600" && char <= "\u06ff")) {
    return "ur";
  }
  if ([...text].some((char) => char >= "\u0900" && char <= "\u097f")) {
    return "ur";
  }
  const letters = [...text].filter((char) => /[A-Za-z\u0600-\u06ff\u0900-\u097f]/.test(char));
  if (letters.length > 0 && letters.every((char) => char <= "\u007f")) {
    return "en";
  }
  if ([...text].some((char) => char.charCodeAt(0) > 127)) {
    return "ur";
  }
  return "en";
}

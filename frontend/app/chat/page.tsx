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

type ServerEvent =
  | { type: "ready"; userId: string; conversationId: string | null }
  | { type: "conversation_created"; conversationId: string }
  | { type: "auth_required"; message: string }
  | { type: "recording_started" }
  | { type: "processing"; stage: "stt" | "agent" | "tts" }
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
  | { type: "debug_memory"; memory: Record<string, unknown> };

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
  jurisdiction?: string | null;
  score?: number | null;
  originalText?: string | null;
  sourceUrl?: string | null;
};

// ── Constants ────────────────────────────────────────────────────────────────

const wsUrl = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/voice";
const apiUrl = wsUrl.replace(/^ws/, "http").replace(/\/ws\/voice$/, "");

// VAD settings (must match backend VadSettings defaults)
const VAD_SILENCE_MS = 950;
const VAD_BARGE_IN_THRESHOLD = 0.25;
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
  const audioQueueRef = useRef<Blob[]>([]);
  const isPlayingRef = useRef(false);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const currentAudioUrlRef = useRef<string | null>(null);
  const lastTurnWasVoiceRef = useRef(false);
  const pendingAutoListenRef = useRef(false);
  const conversationModeRef = useRef(false);
  const recordingStateRef = useRef<RecordingState>("idle");
  const isProcessingRef = useRef(false);
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
  const bargeInDebounceRef = useRef<number | null>(null);

  function resetSessionState() {
    wsRef.current?.close();
    wsRef.current = null;
    stopVad();
    releaseHandsFreeStream();
    stopStream();
    clearPlayback();
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
    if (!conversationMode && recorderRef.current && recorderRef.current.state !== "inactive") {
      stopRecording();
    }
    if (!conversationMode) {
      releaseHandsFreeStream();
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
      clearPlayback();
    };

    return () => {
      socket.close();
      releaseHandsFreeStream();
      stopStream();
      clearPlayback();
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
    audio.onended = () => {
      cleanupCurrentAudio();
      isPlayingRef.current = false;
      // Check for barge-in energy before playing the next sentence
      if (audioQueueRef.current.length > 0) {
        drainAudioQueue();
      } else {
        setIsPlayingTts(false);
        maybeAutoListen();
      }
    };
    audio.onerror = () => {
      cleanupCurrentAudio();
      isPlayingRef.current = false;
      setIsPlayingTts(false);
      drainAudioQueue();
    };
    audio.play().catch(() => {
      cleanupCurrentAudio();
      isPlayingRef.current = false;
      setIsPlayingTts(false);
      drainAudioQueue();
    });
  }

  function cleanupCurrentAudio() {
    if (currentAudioUrlRef.current) {
      URL.revokeObjectURL(currentAudioUrlRef.current);
    }
    currentAudioRef.current = null;
    currentAudioUrlRef.current = null;
  }

  function clearPlayback() {
    currentAudioRef.current?.pause();
    cleanupCurrentAudio();
    audioQueueRef.current = [];
    isPlayingRef.current = false;
    setIsPlayingTts(false);
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
        isProcessingRef.current = true;
        setStatus(
          event.stage === "stt"
            ? "Transcribing…"
            : event.stage === "agent"
              ? "Generating…"
              : "Speaking…",
        );
        setRecordingState("processing");
        break;

      case "transcript":
        lastSpokenLanguageRef.current = event.language;
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
        break;

      case "tts_error":
        // Play error messages without adding to conversation
        playTtsErrorMessages(event.message_en, event.message_ur);
        break;

      case "turn_complete":
        isProcessingRef.current = false;
        setStatus("Ready");
        setRecordingState("idle");
        // In hands-free mode auto-listen after any turn (voice or text)
        if (conversationModeRef.current) {
          pendingAutoListenRef.current = true;
        }
        if (!isPlayingRef.current && audioQueueRef.current.length === 0) {
          setIsPlayingTts(false);
          maybeAutoListen();
        }
        loadConversations();
        break;

      case "voice_mode_end":
        pendingAutoListenRef.current = false;
        isProcessingRef.current = false;
        setConversationMode(false);
        releaseHandsFreeStream();
        setStatus("Voice mode ended");
        setRecordingState("idle");
        break;

      case "cancelled":
        isProcessingRef.current = false;
        clearPlayback();
        if (conversationModeRef.current) {
          pendingAutoListenRef.current = true;
        }
        setStatus("Ready");
        setRecordingState("idle");
        maybeAutoListen();
        break;

      case "error":
        isProcessingRef.current = false;
        setError(event.message);
        setStatus("Ready");
        setRecordingState("idle");
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
          if (isPlayingRef.current) {
            interruptAssistantAndListen();
          } else if (recordingStateRef.current !== "recording") {
            void startRecording();
          }
        } else if (type === "speech_end") {
          if (recordingStateRef.current === "recording") {
            stopRecording();
          }
        } else if (type === "barge_in_candidate") {
          // Debounce to prevent rapid repeated interrupts
          if (bargeInDebounceRef.current) {
            clearTimeout(bargeInDebounceRef.current);
          }

          bargeInDebounceRef.current = window.setTimeout(() => {
            bargeInDebounceRef.current = null;

            // Only interrupt if TTS is still playing
            if (isPlayingRef.current) {
              // Accept the barge-in candidate
              workletNode.port.postMessage({ type: 'accept_barge_in' });
              interruptAssistantAndListen();
            } else {
              // Reject the barge-in candidate (no longer relevant)
              workletNode.port.postMessage({ type: 'reject_barge_in' });
            }
          }, 50); // 50ms debounce to ensure TTS state is stable
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
    setError("");
    setCurrentTranscript("");
    setCurrentResponse("");
    setDetectedLanguage("");
    lastTurnWasVoiceRef.current = true;

    const socket = wsRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setError("WebSocket is not connected.");
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
        return;
      }
      if (conversationModeRef.current) {
        handsFreeStreamRef.current = stream;
      }
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
      recorderRef.current = null;
      isProcessingRef.current = true;
      stopVad();
      if (!conversationModeRef.current) stopStream();
    };

    socket.send(
      JSON.stringify({
        type: "start",
        mimeType: recorder.mimeType,
        languageMode,
      }),
    );
    recorder.start(250);
    recorderRef.current = recorder;
    setRecordingState("recording");

    if (!vadWorkletNodeRef.current) {
      startVad(stream);
    }
  }

  function stopRecording() {
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
    setRecordingState("processing");
    isProcessingRef.current = true;
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
    const socket = wsRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "cancel" }));
    }
    clearPlayback();
    stopVad();
    if (conversationModeRef.current) {
      pendingAutoListenRef.current = true;
    } else {
      pendingAutoListenRef.current = false;
    }
    setRecordingState("idle");
    isProcessingRef.current = false;
    setStatus("Ready");
  }

  function muteAssistantVoice() {
    const socket = wsRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "cancel" }));
    }
    clearPlayback();
    setRecordingState("idle");
    isProcessingRef.current = false;
    setStatus("Ready");
    if (conversationModeRef.current) {
      pendingAutoListenRef.current = true;
      maybeAutoListen();
    }
  }

  function interruptAssistantAndListen() {
    const socket = wsRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "cancel" }));
    }
    clearPlayback();
    stopVad();
    pendingAutoListenRef.current = false;
    setRecordingState("idle");
    isProcessingRef.current = false;
    setStatus("Listening…");
    if (conversationModeRef.current && recordingStateRef.current === "idle") {
      window.setTimeout(() => {
        if (
          conversationModeRef.current &&
          recordingStateRef.current === "idle" &&
          wsRef.current?.readyState === WebSocket.OPEN
        ) {
          startRecording();
        }
      }, 100);
    }
  }

  function sendTextMessage() {
    const text = textDraft.trim();
    const socket = wsRef.current;
    if (!text) return;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      setError("WebSocket is not connected.");
      return;
    }
    setError("");
    setCurrentTranscript(text);
    setCurrentResponse("");
    setTextDraft("");
    setRecordingState("processing");
    setDebugTrace([]);
    setDebugBundle(null);
    lastTurnWasVoiceRef.current = false;

    socket.send(JSON.stringify({ type: "text_message", text, languageMode }));
  }

  function maybeAutoListen() {
    if (!pendingAutoListenRef.current || !conversationModeRef.current) return;
    if (
      !sessionRef.current ||
      connectionRef.current !== "connected" ||
      recordingStateRef.current !== "idle"
    ) {
      return;
    }
    pendingAutoListenRef.current = false;
    // Small delay to let TTS finish and AudioContext drain
    window.setTimeout(() => {
      if (
        conversationModeRef.current &&
        recordingStateRef.current === "idle" &&
        !isPlayingRef.current &&
        wsRef.current?.readyState === WebSocket.OPEN
      ) {
        startRecording();
      }
    }, 500);
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

    if (!response.ok) {
      const message = await response.text();
      if (response.status === 404) {
        await loadConversations();
        return;
      }
      setError(message || "Could not delete conversation.");
      if (convoToRestore) {
        setConversations((prev) => {
          const restored = [convoToRestore, ...prev];
          restored.sort((a, b) => new Date(b.last_message_at || 0).getTime() - new Date(a.last_message_at || 0).getTime());
          return restored;
        });
      }
      return;
    }
    await loadConversations();
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
  const showProcessingBubble = isProcessing && !hasStreamingAgent;
  const canRecord = !!session && isConnected && !isRecording && !isProcessing;

  const barWidth = `${Math.round(vadVolume * 100)}%`;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className={`shell ${sidebarExpanded ? "sidebar-open" : "sidebar-collapsed"}`}>
      {/* Sidebar */}
      <aside className={`sidebar ${sidebarExpanded ? "expanded" : "collapsed"}`}>
        <div className="sidebarTop">
          <div className="brandMark">
            <div className="brandIcon" aria-hidden="true" />
            {sidebarExpanded ? (
              <div>
                <p className="brandEyebrow">Airline Assistant</p>
                <p className="brandName">Claim Desk</p>
              </div>
            ) : null}
          </div>
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
              onClick={() => setConversationMode((value) => !value)}
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
              disabled={!session || !isConnected || isRecording || isProcessing}
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

            {!isRecording && !isProcessing && !isPlayingTts ? (
              <button
                className="sendBtn"
                disabled={!session || !isConnected || !textDraft.trim()}
                onClick={sendTextMessage}
                type="button"
                aria-label="Send text"
              >
                Send
              </button>
            ) : null}

            {isRecording ? (
              <button
                className="micBtn sendVoiceBtn"
                onClick={stopRecording}
                type="button"
                aria-label="Stop recording and send"
              >
                <span className="controlIcon sendVoiceIcon" aria-hidden="true" />
                Send voice
              </button>
            ) : isProcessing ? (
              <button
                className="stopGenerationBtn iconStopBtn"
                onClick={stopAgentGeneration}
                type="button"
                aria-label="Stop generating response"
              >
                <span className="controlIcon stopIcon" aria-hidden="true" />
                Stop generating
              </button>
            ) : isPlayingTts ? (
              <button
                className="muteVoiceBtn iconStopBtn"
                onClick={muteAssistantVoice}
                type="button"
                aria-label="Mute assistant voice"
              >
                <span className="controlIcon muteIcon" aria-hidden="true" />
                Mute voice
              </button>
            ) : (
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

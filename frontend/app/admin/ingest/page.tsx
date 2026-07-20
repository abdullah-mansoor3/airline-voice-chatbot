"use client";

import { useState } from "react";
import Link from "next/link";
import { supabase } from "../../../lib/supabase";

export default function IngestPage() {
  const [step, setStep] = useState<1 | 2>(1);
  const [sourceType, setSourceType] = useState<"url" | "text" | "pdf">("url");
  const [loading, setLoading] = useState(false);
  const [statusText, setStatusText] = useState("");
  const [error, setError] = useState("");

  const [previewData, setPreviewData] = useState<{
    markdown: string;
    document: any;
    chunks_count: number;
    chunks?: { index: number; heading: string; text: string }[];
    name: string;
  } | null>(null);

  const handlePreview = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    setStatusText("Parsing document to Markdown and chunking...");
    
    try {
      const formData = new FormData(e.currentTarget);
      const { data: { session } } = await supabase.auth.getSession();
      
      const apiUrl = (process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/voice").replace(/^ws/, "http").replace(/\/ws\/voice$/, "");
      const res = await fetch(`${apiUrl}/admin/ingest/preview`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session?.access_token}`,
        },
        body: formData,
      });
      
      const json = await res.json();
      if (!res.ok) {
        throw new Error(json.detail || "Preview failed");
      }
      
      setPreviewData(json);
      setStep(2);
      setStatusText("Preview generated successfully.");
    } catch (err: any) {
      setError(`Error: ${err.message}`);
      setStatusText("");
    } finally {
      setLoading(false);
    }
  };

  const handleConfirm = async () => {
    if (!previewData) return;
    
    setLoading(true);
    setError("");
    setStatusText("Upserting chunks to Pinecone and Supabase...");
    
    try {
      const formData = new FormData();
      formData.append("text", previewData.markdown);
      formData.append("source_name", previewData.name);
      
      const { data: { session } } = await supabase.auth.getSession();
      
      const apiUrl = (process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/voice").replace(/^ws/, "http").replace(/\/ws\/voice$/, "");
      const res = await fetch(`${apiUrl}/admin/ingest/confirm`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session?.access_token}`,
        },
        body: formData,
      });
      
      const json = await res.json();
      if (!res.ok) {
        throw new Error(json.detail || "Ingestion failed");
      }
      
      setStatusText(`Success! Ingested ${json.chunks_ingested} chunks for ${previewData.name}.`);
      setPreviewData(null);
      setStep(1);
    } catch (err: any) {
      setError(`Error: ${err.message}`);
      setStatusText("");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ minHeight: "100vh", backgroundColor: "#0f1115", color: "#ececf1", fontFamily: "Inter, sans-serif", padding: "40px 20px" }}>
      <div style={{ maxWidth: "800px", margin: "0 auto" }}>
        
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "40px" }}>
          <div>
            <h1 style={{ fontSize: "28px", fontWeight: "600", margin: "0 0 8px 0" }}>RAG Knowledge Ingestion</h1>
            <p style={{ color: "#a1a1aa", margin: 0 }}>Import policies or web pages into the knowledge base.</p>
          </div>
          <Link href="/chat" style={{ padding: "8px 16px", backgroundColor: "#27272a", color: "#fff", borderRadius: "6px", textDecoration: "none", fontSize: "14px" }}>
            Back to Chat
          </Link>
        </header>

        {statusText && (
          <div style={{ padding: "16px", backgroundColor: "#18181b", borderLeft: "4px solid #3b82f6", borderRadius: "0 6px 6px 0", marginBottom: "24px" }}>
            <p style={{ margin: 0, fontSize: "14px", color: "#bfdbfe" }}>{loading ? "⏳" : "✅"} {statusText}</p>
          </div>
        )}

        {error && (
          <div style={{ padding: "16px", backgroundColor: "#3f1515", borderLeft: "4px solid #ef4444", borderRadius: "0 6px 6px 0", marginBottom: "24px" }}>
            <p style={{ margin: 0, fontSize: "14px", color: "#fca5a5" }}>❌ {error}</p>
          </div>
        )}

        {step === 1 && (
          <div style={{ backgroundColor: "#18181b", padding: "32px", borderRadius: "12px", border: "1px solid #27272a" }}>
            <h2 style={{ fontSize: "20px", marginTop: 0, marginBottom: "24px" }}>Step 1: Parse & Preview</h2>
            <form onSubmit={handlePreview} style={{ display: "flex", flexDirection: "column", gap: "20px" }}>
              
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                <label style={{ fontSize: "14px", fontWeight: "500", color: "#a1a1aa" }}>Source Type</label>
                <select 
                  name="source_type" 
                  value={sourceType} 
                  onChange={(e) => setSourceType(e.target.value as any)}
                  style={{ padding: "12px", backgroundColor: "#27272a", border: "1px solid #3f3f46", color: "#fff", borderRadius: "6px", fontSize: "15px" }}
                >
                  <option value="url">URL (Web Page)</option>
                  <option value="text">Raw Text</option>
                  <option value="pdf">PDF File</option>
                </select>
              </div>
              
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                <label style={{ fontSize: "14px", fontWeight: "500", color: "#a1a1aa" }}>Source Name (Optional)</label>
                <input type="text" name="source_name" placeholder="Title/Name" style={{ padding: "12px", backgroundColor: "#27272a", border: "1px solid #3f3f46", color: "#fff", borderRadius: "6px", fontSize: "15px" }} />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  <label style={{ fontSize: "14px", fontWeight: "500", color: "#a1a1aa" }}>Category (Optional)</label>
                  <input type="text" name="category" placeholder="e.g. customer_refund" style={{ padding: "12px", backgroundColor: "#27272a", border: "1px solid #3f3f46", color: "#fff", borderRadius: "6px", fontSize: "15px" }} />
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  <label style={{ fontSize: "14px", fontWeight: "500", color: "#a1a1aa" }}>Jurisdiction (Optional)</label>
                  <input type="text" name="jurisdiction" placeholder="e.g. US, PK, international" style={{ padding: "12px", backgroundColor: "#27272a", border: "1px solid #3f3f46", color: "#fff", borderRadius: "6px", fontSize: "15px" }} />
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  <label style={{ fontSize: "14px", fontWeight: "500", color: "#a1a1aa" }}>Carrier (Optional)</label>
                  <input type="text" name="carrier" placeholder="e.g. PIA" style={{ padding: "12px", backgroundColor: "#27272a", border: "1px solid #3f3f46", color: "#fff", borderRadius: "6px", fontSize: "15px" }} />
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  <label style={{ fontSize: "14px", fontWeight: "500", color: "#a1a1aa" }}>Regulator (Optional)</label>
                  <input type="text" name="regulator" placeholder="e.g. IATA" style={{ padding: "12px", backgroundColor: "#27272a", border: "1px solid #3f3f46", color: "#fff", borderRadius: "6px", fontSize: "15px" }} />
                </div>
              </div>

              {sourceType === "url" && (
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  <label style={{ fontSize: "14px", fontWeight: "500", color: "#a1a1aa" }}>URL</label>
                  <input type="url" name="url" placeholder="https://example.com" required style={{ padding: "12px", backgroundColor: "#27272a", border: "1px solid #3f3f46", color: "#fff", borderRadius: "6px", fontSize: "15px" }} />
                </div>
              )}
              
              {sourceType === "text" && (
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  <label style={{ fontSize: "14px", fontWeight: "500", color: "#a1a1aa" }}>Raw Text</label>
                  <textarea name="text" placeholder="Paste policy text here..." required style={{ padding: "12px", backgroundColor: "#27272a", border: "1px solid #3f3f46", color: "#fff", borderRadius: "6px", fontSize: "15px", minHeight: "200px", fontFamily: "monospace" }} />
                </div>
              )}
              
              {sourceType === "pdf" && (
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  <label style={{ fontSize: "14px", fontWeight: "500", color: "#a1a1aa" }}>PDF File</label>
                  <input type="file" name="file" accept=".pdf" required style={{ padding: "12px", backgroundColor: "#27272a", border: "1px solid #3f3f46", color: "#fff", borderRadius: "6px", fontSize: "15px" }} />
                </div>
              )}
              
              <button type="submit" disabled={loading} style={{ alignSelf: "flex-start", padding: "12px 24px", backgroundColor: loading ? "#3f3f46" : "#2563eb", color: "#fff", border: "none", borderRadius: "6px", fontSize: "15px", fontWeight: "500", cursor: loading ? "not-allowed" : "pointer", marginTop: "12px", transition: "background-color 0.2s" }}>
                {loading ? "Processing..." : "Generate Preview"}
              </button>
            </form>
          </div>
        )}

        {step === 2 && previewData && (
          <div style={{ backgroundColor: "#18181b", padding: "32px", borderRadius: "12px", border: "1px solid #27272a", display: "flex", flexDirection: "column", gap: "24px" }}>
            <h2 style={{ fontSize: "20px", marginTop: 0, marginBottom: "8px" }}>Step 2: Review & Confirm</h2>
            
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px", backgroundColor: "#27272a", padding: "16px", borderRadius: "8px" }}>
              <div>
                <span style={{ display: "block", fontSize: "12px", color: "#a1a1aa", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: "4px" }}>Title</span>
                <span style={{ fontSize: "15px" }}>{previewData.document.title || "—"}</span>
              </div>
              <div>
                <span style={{ display: "block", fontSize: "12px", color: "#a1a1aa", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: "4px" }}>Total Chunks</span>
                <span style={{ fontSize: "15px", color: "#10b981", fontWeight: "600" }}>{previewData.chunks_count} chunks</span>
              </div>
              <div>
                <span style={{ display: "block", fontSize: "12px", color: "#a1a1aa", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: "4px" }}>Category</span>
                <span style={{ fontSize: "15px" }}>{previewData.document.category?.join(", ") || "—"}</span>
              </div>
              <div>
                <span style={{ display: "block", fontSize: "12px", color: "#a1a1aa", textTransform: "uppercase", letterSpacing: "0.5px", marginBottom: "4px" }}>Jurisdiction</span>
                <span style={{ fontSize: "15px", textTransform: "uppercase" }}>{previewData.document.jurisdiction || "—"}</span>
              </div>
            </div>

            <div>
              <span style={{ display: "block", fontSize: "14px", fontWeight: "500", color: "#a1a1aa", marginBottom: "8px" }}>Markdown Preview (Read-Only)</span>
              <textarea 
                readOnly 
                value={previewData.markdown} 
                style={{ width: "100%", height: "250px", padding: "16px", backgroundColor: "#09090b", border: "1px solid #3f3f46", color: "#d4d4d8", borderRadius: "6px", fontSize: "13px", fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace", resize: "vertical", boxSizing: "border-box" }} 
              />
            </div>

            <div>
              <span style={{ display: "block", fontSize: "14px", fontWeight: "500", color: "#a1a1aa", marginBottom: "8px" }}>Extracted Chunks Preview</span>
              <div style={{ display: "flex", flexDirection: "column", gap: "12px", maxHeight: "400px", overflowY: "auto", padding: "16px", backgroundColor: "#09090b", border: "1px solid #3f3f46", borderRadius: "6px" }}>
                {previewData.chunks?.map(chunk => (
                  <div key={chunk.index} style={{ padding: "12px", backgroundColor: "#18181b", border: "1px solid #27272a", borderRadius: "6px" }}>
                    <div style={{ fontSize: "12px", fontWeight: "600", color: "#3b82f6", marginBottom: "8px", textTransform: "uppercase", letterSpacing: "0.5px" }}>Chunk {chunk.index} • {chunk.heading}</div>
                    <div style={{ fontSize: "13px", color: "#d4d4d8", fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{chunk.text}</div>
                  </div>
                ))}
              </div>
            </div>

            <div style={{ display: "flex", gap: "16px", marginTop: "8px" }}>
              <button 
                type="button" 
                onClick={() => setStep(1)} 
                disabled={loading}
                style={{ padding: "12px 24px", backgroundColor: "#3f3f46", color: "#fff", border: "none", borderRadius: "6px", fontSize: "15px", fontWeight: "500", cursor: loading ? "not-allowed" : "pointer", transition: "background-color 0.2s" }}
              >
                Cancel & Go Back
              </button>
              <button 
                type="button" 
                onClick={handleConfirm} 
                disabled={loading}
                style={{ padding: "12px 24px", backgroundColor: loading ? "#3f3f46" : "#10b981", color: "#fff", border: "none", borderRadius: "6px", fontSize: "15px", fontWeight: "500", cursor: loading ? "not-allowed" : "pointer", transition: "background-color 0.2s" }}
              >
                {loading ? "Processing..." : "Confirm Ingestion"}
              </button>
            </div>
            
          </div>
        )}

      </div>
    </div>
  );
}

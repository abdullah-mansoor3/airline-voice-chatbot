"use client";

import type { Session } from "@supabase/supabase-js";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { supabase } from "../../lib/supabase";

export default function LoginPage() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authMode, setAuthMode] = useState<"signin" | "signup">("signin");
  const [rememberMe, setRememberMe] = useState(true);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      if (data.session) router.replace("/chat");
    });
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession);
      if (nextSession) router.replace("/chat");
    });
    return () => subscription.unsubscribe();
  }, [router]);

  async function submitAuth() {
    setError("");
    setStatus("");

    const action =
      authMode === "signin"
        ? supabase.auth.signInWithPassword({ email, password })
        : supabase.auth.signUp({ email, password });
    const { error: authError } = await action;
    if (authError) {
      setError(authError.message);
      return;
    }

    // Handle session persistence based on rememberMe toggle
    if (!rememberMe) {
      // Add beforeunload listener to clear session on tab close
      const handleBeforeUnload = () => {
        supabase.auth.signOut();
      };
      window.addEventListener('beforeunload', handleBeforeUnload);
      // Store listener reference for cleanup if needed
      (window as any)._authCleanup = () => {
        window.removeEventListener('beforeunload', handleBeforeUnload);
      };
    }

    setStatus(
      authMode === "signin"
        ? "Signed in. Redirecting…"
        : "Account created. Check your email, then sign in.",
    );
  }

  if (session) {
    return (
      <div className="authPage">
        <p className="authStatus">Redirecting to chat…</p>
      </div>
    );
  }

  return (
    <div className="authPage">
      <div className="authCard">
        <Link className="authBack" href="/">
          Back to home
        </Link>
        <div className="authBrand">
          <span className="landingMark" aria-hidden="true" />
          <div>
            <p className="authEyebrow">Claim Desk</p>
            <h1 className="authTitle">
              {authMode === "signin" ? "Sign in" : "Create account"}
            </h1>
          </div>
        </div>

        <form
          className="authForm"
          onSubmit={(event) => {
            event.preventDefault();
            submitAuth();
          }}
        >
          <label className="authField">
            <span>Email</span>
            <input
              autoComplete="email"
              className="input"
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
              type="email"
              value={email}
            />
          </label>
          <label className="authField">
            <span>Password</span>
            <input
              autoComplete={authMode === "signin" ? "current-password" : "new-password"}
              className="input"
              onChange={(event) => setPassword(event.target.value)}
              placeholder="••••••••"
              type="password"
              value={password}
            />
          </label>

          <label className="authField" style={{ flexDirection: 'row', alignItems: 'center', gap: '0.5rem' }}>
            <input
              type="checkbox"
              checked={rememberMe}
              onChange={(event) => setRememberMe(event.target.checked)}
              style={{ width: 'auto', margin: 0 }}
            />
            <span style={{ margin: 0 }}>Keep me signed in</span>
          </label>

          {error ? <p className="authError">{error}</p> : null}
          {status ? <p className="authStatus">{status}</p> : null}

          <button className="btnPrimary authSubmit" type="submit">
            {authMode === "signin" ? "Sign in" : "Create account"}
          </button>
        </form>

        <button
          className="btnGhost authToggle"
          onClick={() =>
            setAuthMode(authMode === "signin" ? "signup" : "signin")
          }
          type="button"
        >
          {authMode === "signin"
            ? "Need an account? Sign up"
            : "Already have an account? Sign in"}
        </button>
      </div>
    </div>
  );
}

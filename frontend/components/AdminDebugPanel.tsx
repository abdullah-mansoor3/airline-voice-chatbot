"use client";

import { useState } from "react";

type AdminTab = "chain" | "memory" | "users";

type Props = {
  debugBundle: Record<string, unknown> | null;
  debugTrace: Record<string, unknown>[];
  debugMemory: Record<string, unknown> | null;
  adminDbData: Record<string, unknown> | null;
  onClear: () => void;
};

function asString(value: unknown): string {
  if (value == null) return "—";
  return String(value);
}

function asList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item));
}

function ChainStep({ entry, index }: { entry: Record<string, unknown>; index: number }) {
  const node = asString(entry.node);
  const step = asString(entry.step);

  return (
    <article className="debugStep">
      <header className="debugStepHeader">
        <span className="debugStepIndex">{index + 1}</span>
        <div>
          <h4 className="debugStepTitle">{node}</h4>
          {step !== "—" && <p className="debugStepSubtitle">{step}</p>}
        </div>
      </header>

      {entry.reasoning || entry.internal_reasoning ? (
        <section className="debugBlock">
          <h5>Reasoning</h5>
          <p>{asString(entry.reasoning || entry.internal_reasoning)}</p>
        </section>
      ) : null}

      {entry.planned_tools ? (
        <section className="debugBlock">
          <h5>Planned tools</h5>
          <ul className="debugList">
            {(entry.planned_tools as Record<string, unknown>[]).map((tool, i) => (
              <li key={i}>
                <strong>{asString(tool.name)}</strong>
                {tool.args ? (
                  <code className="debugInlineCode">
                    {JSON.stringify(tool.args)}
                  </code>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {entry.rag_query_rewrite ? (
        <section className="debugBlock">
          <h5>RAG query rewrite</h5>
          <dl className="debugKv">
            <div>
              <dt>Original</dt>
              <dd>{asString((entry.rag_query_rewrite as Record<string, unknown>).original_query)}</dd>
            </div>
            <div>
              <dt>Rewritten</dt>
              <dd>{asString((entry.rag_query_rewrite as Record<string, unknown>).rag_query)}</dd>
            </div>
            <div>
              <dt>Note</dt>
              <dd>{asString((entry.rag_query_rewrite as Record<string, unknown>).rewrite_note)}</dd>
            </div>
          </dl>
        </section>
      ) : null}

      {entry.tools_called ? (
        <section className="debugBlock">
          <h5>Tools called</h5>
          <p className="debugTags">
            {asList(entry.tools_called).map((tool) => (
              <span className="debugTag" key={tool}>
                {tool}
              </span>
            ))}
          </p>
        </section>
      ) : null}

      {entry.tool_results ? (
        <section className="debugBlock">
          <h5>Tool results</h5>
          <ToolResultsSummary results={entry.tool_results as Record<string, unknown>} />
        </section>
      ) : null}

      {entry.validation_warnings ? (
        <section className="debugBlock">
          <h5>Validation</h5>
          <dl className="debugKv">
            <div>
              <dt>Confidence</dt>
              <dd>{asString(entry.confidence)}</dd>
            </div>
            <div>
              <dt>Escalation</dt>
              <dd>{entry.needs_escalation ? "Yes" : "No"}</dd>
            </div>
            <div>
              <dt>Warnings</dt>
              <dd>{asList(entry.validation_warnings).join(", ") || "None"}</dd>
            </div>
            <div>
              <dt>Citations</dt>
              <dd>{asList(entry.cited_chunk_ids).join(", ") || "None"}</dd>
            </div>
          </dl>
          {entry.answer_preview ? (
            <p className="debugPreview">{asString(entry.answer_preview)}</p>
          ) : null}
        </section>
      ) : null}
    </article>
  );
}

function ToolResultsSummary({ results }: { results: Record<string, unknown> }) {
  const policy = results.search_policy as Record<string, unknown> | undefined;
  const flights = results.search_alternative_flights as Record<string, unknown> | undefined;
  const datetime = results.get_current_datetime as Record<string, unknown> | undefined;
  const order = results.load_order_context as Record<string, unknown> | undefined;

  return (
    <div className="debugToolGrid">
      {policy ? (
        <div className="debugToolCard">
          <h6>Policy</h6>
          <p>{asString(policy.chunk_count)} chunks</p>
        </div>
      ) : null}
      {flights ? (
        <div className="debugToolCard">
          <h6>Flights</h6>
          <p>{asString(flights.offer_count)} offers</p>
        </div>
      ) : null}
      {datetime ? (
        <div className="debugToolCard">
          <h6>Datetime</h6>
          <p>{asString(datetime.date)} {asString(datetime.time)}</p>
        </div>
      ) : null}
      {order ? (
        <div className="debugToolCard">
          <h6>Order</h6>
          <p>{Object.keys(order).length ? "Loaded" : "Empty"}</p>
        </div>
      ) : null}
    </div>
  );
}

function MemoryView({ memory }: { memory: Record<string, unknown> }) {
  const recent = (memory.recent_messages as Record<string, unknown>[]) || [];
  const facts = (memory.long_term_facts as Record<string, unknown>[]) || [];
  const summary = asString(memory.summary);

  return (
    <div className="debugMemoryView">
      {summary !== "—" ? (
        <section className="debugBlock">
          <h5>Summary</h5>
          <p>{summary}</p>
        </section>
      ) : null}
      {recent.length > 0 ? (
        <section className="debugBlock">
          <h5>Recent messages</h5>
          <ul className="debugList">
            {recent.map((msg, i) => (
              <li key={i}>
                <strong>{asString(msg.speaker || msg.role)}:</strong>{" "}
                {asString(msg.text || msg.content)}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {facts.length > 0 ? (
        <section className="debugBlock">
          <h5>Facts</h5>
          <ul className="debugList">
            {facts.map((fact, i) => (
              <li key={i}>
                <strong>{asString(fact.memory_key)}:</strong>{" "}
                {asString(fact.memory_value)}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function CurrentUserView({ data }: { data: Record<string, unknown> }) {
  const user = (data.user as Record<string, unknown>) || {};
  const memories = (data.memories as Record<string, unknown>[]) || [];
  const conversations = (data.conversations as Record<string, unknown>[]) || [];

  return (
    <div className="debugUsersView">
      <section className="debugBlock">
        <h5>Current user</h5>
        <dl className="debugKv">
          <div>
            <dt>Email</dt>
            <dd>{asString(user.email || user.id)}</dd>
          </div>
          <div>
            <dt>Name</dt>
            <dd>{asString(user.full_name || "—")}</dd>
          </div>
          <div>
            <dt>Role</dt>
            <dd>{asString(user.role || "user")}</dd>
          </div>
          <div>
            <dt>Language</dt>
            <dd>{asString(user.preferred_language || "—")}</dd>
          </div>
        </dl>
      </section>
      <section className="debugBlock">
        <h5>Your memories ({memories.length})</h5>
        <ul className="debugList compact">
          {memories.slice(0, 6).map((memory, index) => (
            <li key={index}>
              {asString(memory.memory_key)}: {asString(memory.memory_value)}
            </li>
          ))}
        </ul>
      </section>
      <section className="debugBlock">
        <h5>Your conversations ({conversations.length})</h5>
        <ul className="debugList compact">
          {conversations.slice(0, 8).map((convo) => (
            <li key={asString(convo.id)}>
              {asString(convo.title || "Untitled")} · {asString(convo.status)}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

export function AdminDebugPanel({
  debugBundle,
  debugTrace,
  debugMemory,
  adminDbData,
  onClear,
}: Props) {
  const [tab, setTab] = useState<AdminTab>("chain");
  const chain =
    (debugBundle?.reasoning_chain as Record<string, unknown>[]) || debugTrace;
  const memory =
    (debugMemory as Record<string, unknown> | null) ||
    (debugBundle?.memory as Record<string, unknown> | undefined) ||
    null;

  return (
    <section className="adminSection">
      <div className="sidebarHeading">Admin</div>
      <div className="adminTabs">
        {(
          [
            ["chain", "Chain"],
            ["memory", "Memory"],
            ["users", "Account"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            className={`adminTabBtn ${tab === id ? "active" : ""}`}
            onClick={() => setTab(id)}
            type="button"
          >
            {label}
          </button>
        ))}
      </div>

      <div className="adminPane">
        <div className="adminPaneHeader">
          {tab === "chain" ? "Reasoning chain" : tab === "memory" ? "Memory" : "Database"}
          <button className="adminClearBtn" onClick={onClear} type="button">
            Clear
          </button>
        </div>

        <div className="adminPaneBody">
          {tab === "chain" ? (
            chain.length === 0 ? (
              <p className="adminEmpty">Send a message to see the reasoning chain.</p>
            ) : (
              <div className="debugChain">
                {debugBundle?.query ? (
                  <div className="debugQueryCard">
                    <span className="debugQueryLabel">Query</span>
                    <p>{asString(debugBundle.query)}</p>
                  </div>
                ) : null}
                {debugBundle?.internal_reasoning ? (
                  <div className="debugReasoningCard">
                    <span className="debugQueryLabel">Internal reasoning</span>
                    <p>{asString(debugBundle.internal_reasoning)}</p>
                  </div>
                ) : null}
                {chain.map((entry, index) => (
                  <ChainStep entry={entry} index={index} key={index} />
                ))}
              </div>
            )
          ) : null}

          {tab === "memory" ? (
            memory ? (
              <MemoryView memory={memory} />
            ) : (
              <p className="adminEmpty">No memory captured yet.</p>
            )
          ) : null}

          {tab === "users" ? (
            adminDbData ? (
              <CurrentUserView data={adminDbData} />
            ) : (
              <p className="adminEmpty">Loading account snapshot…</p>
            )
          ) : null}
        </div>
      </div>
    </section>
  );
}

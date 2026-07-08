create extension if not exists pgcrypto;

create table if not exists public.users (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text,
  phone text,
  preferred_language text
);

create table if not exists public.orders (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references public.users(id) on delete cascade,
  duffel_order_id text,
  order_type text,
  amount numeric,
  fare_class text,
  status text
);

create table if not exists public.disputes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references public.users(id) on delete cascade,
  order_id uuid references public.orders(id) on delete set null,
  claim_type text,
  detected_language text,
  status text,
  created_at timestamp with time zone not null default now()
);

create table if not exists public.policy_documents (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  version text,
  effective_date date,
  jurisdiction text not null,
  category text not null
);

create table if not exists public.policy_chunks (
  id uuid primary key default gen_random_uuid(),
  policy_document_id uuid not null references public.policy_documents(id) on delete cascade,
  pinecone_vector_id text unique,
  chunk_index int not null,
  chunk_text text not null
);

create table if not exists public.dispute_transcripts (
  id uuid primary key default gen_random_uuid(),
  dispute_id uuid not null references public.disputes(id) on delete cascade,
  turn_index int not null,
  speaker text not null check (speaker in ('user', 'agent')),
  original_text text,
  english_text text
);

create table if not exists public.dispute_actions (
  id uuid primary key default gen_random_uuid(),
  dispute_id uuid not null references public.disputes(id) on delete cascade,
  cited_chunk_id uuid references public.policy_chunks(id) on delete restrict,
  action_type text not null check (
    action_type in ('approve_refund', 'reject', 'request_info', 'escalate')
  ),
  refund_amount numeric,
  executed_by text not null check (executed_by in ('ai', 'human_override')),
  created_at timestamp with time zone not null default now()
);

create table if not exists public.dispute_action_citations (
  action_id uuid not null references public.dispute_actions(id) on delete cascade,
  chunk_id uuid not null references public.policy_chunks(id) on delete restrict,
  primary key (action_id, chunk_id)
);

alter table public.users enable row level security;
alter table public.orders enable row level security;
alter table public.disputes enable row level security;
alter table public.dispute_transcripts enable row level security;
alter table public.dispute_actions enable row level security;
alter table public.dispute_action_citations enable row level security;
alter table public.policy_documents enable row level security;
alter table public.policy_chunks enable row level security;

create policy "users can read own profile"
  on public.users for select
  using (auth.uid() = id);

create policy "users can update own profile"
  on public.users for update
  using (auth.uid() = id)
  with check (auth.uid() = id);

create policy "users can read own orders"
  on public.orders for select
  using (auth.uid() = user_id);

create policy "users can read own disputes"
  on public.disputes for select
  using (auth.uid() = user_id);

create policy "users can read own transcripts"
  on public.dispute_transcripts for select
  using (
    exists (
      select 1 from public.disputes
      where disputes.id = dispute_transcripts.dispute_id
        and disputes.user_id = auth.uid()
    )
  );

create policy "users can read own actions"
  on public.dispute_actions for select
  using (
    exists (
      select 1 from public.disputes
      where disputes.id = dispute_actions.dispute_id
        and disputes.user_id = auth.uid()
    )
  );

create policy "users can read own action citations"
  on public.dispute_action_citations for select
  using (
    exists (
      select 1
      from public.dispute_actions
      join public.disputes on disputes.id = dispute_actions.dispute_id
      where dispute_actions.id = dispute_action_citations.action_id
        and disputes.user_id = auth.uid()
    )
  );

create policy "policy documents are readable"
  on public.policy_documents for select
  using (true);

create policy "policy chunks are readable"
  on public.policy_chunks for select
  using (true);

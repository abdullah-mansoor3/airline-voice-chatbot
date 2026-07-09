create extension if not exists pgcrypto;

create table if not exists public.users (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text,
  phone text,
  preferred_language text,
  role text not null default 'user' check (role in ('user', 'admin'))
);

create table if not exists public.conversations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  title text,
  status text not null default 'active' check (
    status in ('active', 'closed', 'escalated')
  ),
  primary_language text,
  short_term_summary text,
  memory_updated_at timestamp with time zone,
  started_at timestamp with time zone not null default now(),
  last_message_at timestamp with time zone not null default now(),
  unique (user_id, title)
);

alter table public.conversations
  add column if not exists short_term_summary text,
  add column if not exists memory_updated_at timestamp with time zone;

do $$
begin
  with duplicates as (
    select
      id,
      title,
      row_number() over (
        partition by user_id, title
        order by started_at, id
      ) as duplicate_index
    from public.conversations
    where title is not null
  )
  update public.conversations
  set title = left(duplicates.title, 72) || ' (' || duplicates.duplicate_index || ')'
  from duplicates
  where conversations.id = duplicates.id
    and duplicates.duplicate_index > 1;

  if not exists (
    select 1 from pg_constraint
    where conname = 'conversations_user_id_title_key'
      and conrelid = 'public.conversations'::regclass
  ) then
    alter table public.conversations
      add constraint conversations_user_id_title_key unique (user_id, title);
  end if;
end $$;

create table if not exists public.orders (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references public.users(id) on delete cascade,
  duffel_order_id text,
  booking_reference text,
  airline text,
  origin text,
  destination text,
  departure_date date,
  order_type text,
  amount numeric,
  fare_class text,
  status text,
  raw_payload jsonb,
  updated_at timestamp with time zone not null default now()
);

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'orders_duffel_order_id_key'
      and conrelid = 'public.orders'::regclass
  ) then
    alter table public.orders
      add constraint orders_duffel_order_id_key unique (duffel_order_id);
  end if;

  if not exists (
    select 1 from pg_constraint
    where conname = 'orders_user_booking_reference_key'
      and conrelid = 'public.orders'::regclass
  ) then
    alter table public.orders
      add constraint orders_user_booking_reference_key unique (user_id, booking_reference);
  end if;
end $$;

alter table public.orders
  add column if not exists booking_reference text,
  add column if not exists airline text,
  add column if not exists origin text,
  add column if not exists destination text,
  add column if not exists departure_date date,
  add column if not exists raw_payload jsonb,
  add column if not exists updated_at timestamp with time zone not null default now();

create table if not exists public.user_memories (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  memory_key text not null,
  memory_value text not null,
  confidence numeric not null default 0.8,
  source text not null default 'conversation',
  created_at timestamp with time zone not null default now(),
  last_seen_at timestamp with time zone not null default now(),
  unique (user_id, memory_key)
);

create table if not exists public.disputes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references public.users(id) on delete cascade,
  order_id uuid references public.orders(id) on delete set null,
  conversation_id uuid references public.conversations(id) on delete set null,
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
  category text not null,
  unique (title, version, jurisdiction)
);

create table if not exists public.policy_chunks (
  id uuid primary key default gen_random_uuid(),
  policy_document_id uuid not null references public.policy_documents(id) on delete cascade,
  pinecone_vector_id text unique,
  chunk_index int not null,
  chunk_text text not null,
  unique (policy_document_id, chunk_index)
);

create table if not exists public.messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  dispute_id uuid references public.disputes(id) on delete set null,
  turn_index int not null,
  speaker text not null check (speaker in ('user', 'agent')),
  original_text text,
  english_text text,
  audio_url text,
  created_at timestamp with time zone not null default now(),
  unique (conversation_id, turn_index, speaker)
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
alter table public.conversations enable row level security;
alter table public.orders enable row level security;
alter table public.user_memories enable row level security;
alter table public.disputes enable row level security;
alter table public.messages enable row level security;
alter table public.dispute_actions enable row level security;
alter table public.dispute_action_citations enable row level security;
alter table public.policy_documents enable row level security;
alter table public.policy_chunks enable row level security;

-- Grant table-level permissions to Supabase roles.
-- Tables created via SQL Editor (not the UI) don't get these automatically.
-- service_role bypasses RLS but still needs table-level GRANT.
grant usage on schema public to anon, authenticated, service_role;

grant all on all tables    in schema public to service_role;
grant all on all sequences in schema public to service_role;

grant select, insert, update, delete on all tables    in schema public to authenticated;
grant usage, select                  on all sequences in schema public to authenticated;

grant select on all tables in schema public to anon;

create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.users
    where users.id = auth.uid()
      and users.role = 'admin'
  );
$$;

create or replace function public.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.users (id, full_name, phone, preferred_language)
  values (
    new.id,
    coalesce(new.raw_user_meta_data->>'full_name', new.raw_user_meta_data->>'name'),
    new.phone,
    new.raw_user_meta_data->>'preferred_language'
  )
  on conflict (id) do nothing;

  return new;
end;
$$;

create or replace function public.prevent_user_role_escalation()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.role is distinct from old.role
    and coalesce(auth.jwt()->>'role', '') <> 'service_role'
    and current_user not in ('postgres', 'supabase_admin')
    and not public.is_admin()
  then
    raise exception 'Only admins can change user roles.';
  end if;

  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_auth_user();

drop trigger if exists prevent_user_role_escalation on public.users;
create trigger prevent_user_role_escalation
  before update of role on public.users
  for each row execute function public.prevent_user_role_escalation();

drop policy if exists "users can read own profile" on public.users;
create policy "users can read own profile"
  on public.users for select
  using (auth.uid() = id or public.is_admin());

drop policy if exists "users can update own profile" on public.users;
create policy "users can update own profile"
  on public.users for update
  using (auth.uid() = id)
  with check (auth.uid() = id);

drop policy if exists "admins can manage profiles" on public.users;
create policy "admins can manage profiles"
  on public.users for all
  using (public.is_admin())
  with check (public.is_admin());

drop policy if exists "users can read own conversations" on public.conversations;
create policy "users can read own conversations"
  on public.conversations for select
  using (auth.uid() = user_id or public.is_admin());

drop policy if exists "users can create own conversations" on public.conversations;
create policy "users can create own conversations"
  on public.conversations for insert
  with check (auth.uid() = user_id);

drop policy if exists "users can update own conversations" on public.conversations;
create policy "users can update own conversations"
  on public.conversations for update
  using (auth.uid() = user_id or public.is_admin())
  with check (auth.uid() = user_id or public.is_admin());

drop policy if exists "users can delete own conversations" on public.conversations;
create policy "users can delete own conversations"
  on public.conversations for delete
  using (auth.uid() = user_id or public.is_admin());

drop policy if exists "users can read own orders" on public.orders;
create policy "users can read own orders"
  on public.orders for select
  using (auth.uid() = user_id or public.is_admin());

drop policy if exists "users can read own memories" on public.user_memories;
create policy "users can read own memories"
  on public.user_memories for select
  using (auth.uid() = user_id or public.is_admin());

drop policy if exists "users can update own memories" on public.user_memories;
create policy "users can update own memories"
  on public.user_memories for update
  using (auth.uid() = user_id or public.is_admin())
  with check (auth.uid() = user_id or public.is_admin());

drop policy if exists "users can delete own memories" on public.user_memories;
create policy "users can delete own memories"
  on public.user_memories for delete
  using (auth.uid() = user_id or public.is_admin());

drop policy if exists "users can read own disputes" on public.disputes;
create policy "users can read own disputes"
  on public.disputes for select
  using (auth.uid() = user_id or public.is_admin());

drop policy if exists "users can read own messages" on public.messages;
create policy "users can read own messages"
  on public.messages for select
  using (
    exists (
      select 1 from public.conversations
      where conversations.id = messages.conversation_id
        and conversations.user_id = auth.uid()
    )
    or public.is_admin()
  );

drop policy if exists "users can read own actions" on public.dispute_actions;
create policy "users can read own actions"
  on public.dispute_actions for select
  using (
    exists (
      select 1 from public.disputes
      where disputes.id = dispute_actions.dispute_id
        and disputes.user_id = auth.uid()
    )
    or public.is_admin()
  );

drop policy if exists "users can read own action citations" on public.dispute_action_citations;
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
    or public.is_admin()
  );

drop policy if exists "policy documents are readable" on public.policy_documents;
create policy "policy documents are readable"
  on public.policy_documents for select
  using (true);

drop policy if exists "policy chunks are readable" on public.policy_chunks;
create policy "policy chunks are readable"
  on public.policy_chunks for select
  using (true);

-- Create the admin in Supabase Auth first, then promote that profile with the service role:
-- update public.users set role = 'admin' where id = '<admin-auth-user-uuid>';

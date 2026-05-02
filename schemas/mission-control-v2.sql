-- Mission Control v2 canonical state contract.
-- Dashboard-safe data only. Do not store secrets, raw connector payloads,
-- OAuth tokens, raw emails, or private account contents in these tables.

create table if not exists public.mc_v2_agents (
  id text primary key,
  label text not null,
  role text not null default '',
  owner text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.mc_v2_agent_status (
  agent_id text primary key references public.mc_v2_agents(id) on delete cascade,
  status text not null check (status in ('active', 'ready', 'done', 'blocked', 'error', 'info', 'offline')),
  objective text not null default '',
  detail text not null default '',
  current_tool text not null default '',
  active boolean not null default false,
  updated_at timestamptz not null default now(),
  source text not null default 'mission-control-v2',
  steps jsonb not null default '[]'::jsonb,
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists public.mc_v2_events (
  id text primary key,
  agent_id text not null references public.mc_v2_agents(id) on delete cascade,
  event_type text not null check (event_type in ('status', 'job', 'decision', 'handoff', 'blocked', 'complete', 'note', 'heartbeat')),
  status text not null check (status in ('active', 'queued', 'accepted', 'done', 'blocked', 'error', 'info', 'cancelled')),
  title text not null,
  detail text not null default '',
  tool text not null default '',
  privacy text not null default 'dashboard-safe',
  created_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists public.mc_v2_jobs (
  id text primary key,
  event_id text references public.mc_v2_events(id) on delete set null,
  agent_id text not null references public.mc_v2_agents(id) on delete cascade,
  title text not null,
  status text not null check (status in ('queued', 'active', 'done', 'blocked', 'error', 'cancelled', 'info')),
  detail text not null default '',
  tool text not null default '',
  started_at timestamptz,
  completed_at timestamptz,
  updated_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists public.mc_v2_approvals (
  id text primary key,
  agent_id text references public.mc_v2_agents(id) on delete set null,
  title text not null,
  detail text not null default '',
  requested_by text not null default 'joshex',
  status text not null default 'pending' check (status in ('pending', 'approved', 'rejected', 'cancelled')),
  risk_tier text not null default 'dashboard-safe',
  created_at timestamptz not null default now(),
  decided_at timestamptz,
  metadata jsonb not null default '{}'::jsonb
);

create index if not exists mc_v2_events_agent_created_idx on public.mc_v2_events(agent_id, created_at desc);
create index if not exists mc_v2_jobs_agent_updated_idx on public.mc_v2_jobs(agent_id, updated_at desc);
create index if not exists mc_v2_approvals_status_created_idx on public.mc_v2_approvals(status, created_at desc);

alter table public.mc_v2_agents enable row level security;
alter table public.mc_v2_agent_status enable row level security;
alter table public.mc_v2_events enable row level security;
alter table public.mc_v2_jobs enable row level security;
alter table public.mc_v2_approvals enable row level security;

drop policy if exists "mc_v2 read dashboard safe agents" on public.mc_v2_agents;
create policy "mc_v2 read dashboard safe agents"
  on public.mc_v2_agents for select
  to anon, authenticated
  using (true);

drop policy if exists "mc_v2 read dashboard safe status" on public.mc_v2_agent_status;
create policy "mc_v2 read dashboard safe status"
  on public.mc_v2_agent_status for select
  to anon, authenticated
  using (true);

drop policy if exists "mc_v2 read dashboard safe events" on public.mc_v2_events;
create policy "mc_v2 read dashboard safe events"
  on public.mc_v2_events for select
  to anon, authenticated
  using (privacy = 'dashboard-safe');

drop policy if exists "mc_v2 read dashboard safe jobs" on public.mc_v2_jobs;
create policy "mc_v2 read dashboard safe jobs"
  on public.mc_v2_jobs for select
  to anon, authenticated
  using (true);

drop policy if exists "mc_v2 read pending approvals" on public.mc_v2_approvals;
create policy "mc_v2 read pending approvals"
  on public.mc_v2_approvals for select
  to anon, authenticated
  using (risk_tier = 'dashboard-safe');

-- Writes should be performed by trusted publishers through service-side scripts.
-- Do not add anonymous write policies for v2 operational tables.

insert into public.mc_v2_agents (id, label, role, owner)
values
  ('joshex', 'JOSHeX', 'Personal Codex, approvals, private connectors, final integration', 'Josh'),
  ('josh', 'JOSH 2.0', 'Mission Control host, OpenCLAW services, local operations', 'Josh 2.0'),
  ('jaimes', 'JAIMES', 'Hermes reports, specialist analysis, model-heavy work', 'JAIMES'),
  ('jain', 'J.A.I.N', 'Scheduled workers, monitors, intelligence scans', 'J.A.I.N')
on conflict (id) do update set
  label = excluded.label,
  role = excluded.role,
  owner = excluded.owner,
  updated_at = now();

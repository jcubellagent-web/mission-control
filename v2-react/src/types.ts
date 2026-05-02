export type AgentId = "joshex" | "josh" | "jaimes" | "jain";

export type AgentStatus = {
  agent_id: AgentId;
  status: string;
  objective: string;
  detail: string;
  current_tool: string;
  active: boolean;
  updated_at: string;
  steps: Array<{
    label?: string;
    title?: string;
    status?: string;
    tool?: string;
    kind?: string;
  }>;
};

export type AgentEvent = {
  id: string;
  agent_id: AgentId;
  event_type: string;
  status: string;
  title: string;
  detail: string;
  tool: string;
  privacy?: string;
  created_at: string;
  metadata?: Record<string, unknown>;
};

export type AgentJob = {
  id: string;
  event_id?: string;
  agent_id: AgentId;
  title: string;
  status: string;
  detail: string;
  tool: string;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at: string;
};

export type Approval = {
  id: string;
  agent_id: AgentId;
  title: string;
  detail: string;
  requested_by: AgentId;
  status: string;
  risk_tier: string;
  created_at: string;
};

export type MissionControlState = {
  source: string;
  statuses: AgentStatus[];
  events: AgentEvent[];
  jobs: AgentJob[];
  approvals: Approval[];
  modelUsage?: ModelUsage;
  signals: SignalItem[];
};

export type ModelUsage = {
  session?: number;
  daily?: number;
  weekly?: number;
  monthly?: number;
  lastUpdated?: string;
  topModels?: Array<{ name: string; window?: string; cost?: number }>;
  breakdown?: Array<{
    name: string;
    source?: string;
    weeklyCost?: number;
    dailyCost?: number;
    sessionCost?: number;
    costEstimated?: boolean;
  }>;
  weeklyRunRate?: {
    total?: number;
    automation?: number;
    interactive?: number;
    projectedMonthly?: number;
  };
  elevenlabs?: {
    chars_used?: number;
    chars_limit?: number;
    available?: boolean;
  };
  jainApi?: {
    daily?: number;
    weekly?: number;
    monthly?: number;
    available?: boolean;
    stale?: boolean;
    lastError?: string;
  };
};

export type SignalItem = {
  id: string;
  label: string;
  title: string;
  reason: string;
  source: string;
  score?: number;
  time?: string;
  url?: string;
};

declare global {
  interface Window {
    MC_V2_CONFIG?: {
      supabaseUrl?: string;
      supabaseKey?: string;
    };
  }
}

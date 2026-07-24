export type AgentId = "joshex" | "josh2" | "jaimes" | "jain";

export type AgentStatus = {
  agent_id: AgentId;
  status: string;
  objective: string;
  detail: string;
  current_tool: string;
  model?: string;
  model_family?: CanonicalModelFamily;
  route_verified?: boolean;
  work_id?: string;
  run_id?: string;
  phase?: string;
  lease_until?: string;
  origin_claim_hash?: string;
  source?: string;
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

export type CanonicalModelFamily = "codex" | "antigravity" | "ollama" | "grok";

export type ActiveModelRoute = {
  workId: string;
  runId: string;
  ownerAgent: AgentId;
  modelFamily: CanonicalModelFamily;
  modelId: string;
  routeVerified: true;
  executionRole?: "controller" | "worker";
  controllerWorkId?: string;
  controllerRunId?: string;
  activatedAt?: string;
  updatedAt: string;
  leaseUntil?: string;
  sourceEventId?: string;
  revision?: number;
};

export type ActiveWork = {
  workId: string;
  runId: string;
  generation: number;
  sequence: number;
  status: string;
  ownerAgent: AgentId;
  ownerLabel?: string;
  objective: string;
  phase: string;
  tool: string;
  detail: string;
  origin?: string;
  originClaimHash?: string;
  modelFamily?: CanonicalModelFamily | null;
  modelId?: string | null;
  routeVerified: boolean;
  executionRole?: "controller" | "worker";
  controllerWorkId?: string | null;
  controllerRunId?: string | null;
  leaseUntil?: string;
  createdAt: string;
  updatedAt: string;
  lastMeaningfulAt?: string;
  stale?: boolean;
};

export type ControlTowerHot = {
  schemaVersion?: number;
  revision?: number;
  generatedAt?: string;
  storeUpdatedAt?: string;
  source?: string;
  freshness?: Record<string, unknown>;
  counts?: Record<string, unknown>;
  activeWorks: ActiveWork[];
  activeModelRoutes: ActiveModelRoute[];
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
  schedule?: string;
  sourceLabel?: string;
  runStatus?: string;
  lastRun?: string;
  nextRun?: string;
  verifiedToday?: boolean;
  todayRelevant?: boolean;
};

export type TodayJobOutcome = "complete" | "skipped" | "broken" | "pending";

export type TodayJobEvidence = {
  source?: string;
  status?: string;
  at?: string | null;
  summary?: string | null;
};

export type TodayJobOccurrence = {
  occurrenceId: string;
  definitionId?: string;
  name: string;
  owner?: string;
  agent?: string;
  source?: string;
  sourceLabel?: string;
  category?: string;
  description?: string;
  scheduledAt?: string;
  scheduledTime?: string;
  schedule?: string;
  outcome: TodayJobOutcome;
  runStatus?: string;
  lastRun?: string;
  durationMs?: number;
  duration?: string;
  evidence?: string | TodayJobEvidence;
  rolledUp?: boolean;
  expectedRuns?: number;
  completedRuns?: number;
};

export type TodayJobsMeta = {
  version?: number | string;
  timezone?: string;
  date?: string;
  generatedAt?: string;
  now?: string;
  nowIndex?: number;
  nextOccurrenceId?: string;
  counts?: Partial<Record<TodayJobOutcome, number>>;
  definitionCount?: number;
  occurrenceCount?: number;
  rolledUpDefinitionCount?: number;
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

export type OperationalAlert = {
  id: string;
  title: string;
  detail: string;
  priority: string;
  kind: string;
  url: string;
  created_at: string;
};

export type BrainAtlasNode = {
  id: string;
  kind: "agent" | "work" | "receipt" | "model";
  label: string;
  status?: "accepted" | "planned" | "routed" | "active" | "verifying" | "done" | "blocked" | "error" | "cancelled";
  observedAt: string;
  receiptCount: number;
  generation?: number;
  sequence?: number;
  routeVerified?: boolean;
  family?: "codex" | "antigravity" | "ollama" | "grok";
  modelId?: string;
};

export type BrainAtlasEdge = {
  id: string;
  kind: "owns" | "emitted" | "verified-route";
  source: string;
  target: string;
  evidenceReceipt: string;
  observedAt: string;
};

export type BrainAtlas = {
  schemaVersion: 1;
  generatedAt: string;
  status: "ready" | "empty" | "unavailable";
  empty: boolean;
  emptyReason: string | null;
  source: {
    name: "control-tower-work-ledger";
    verified: boolean;
    schemaVersion: number | null;
    revision: number | null;
  };
  window: { days: 7; start: string | null; end: string | null };
  limits: { maxNodes: number; hardMaxNodes: 100 };
  counts: {
    nodes: number;
    edges: number;
    agents: number;
    works: number;
    receipts: number;
    models: number;
    sourceRowsInWindow: number;
    excluded: {
      timeOutOfWindow: number;
      legacyOrInvalid: number;
      capacityReceipts: number;
      capacityRoutes: number;
      unverifiedRoutes: number;
      unsafeVerifiedRoutes: number;
    };
  };
  nodes: BrainAtlasNode[];
  edges: BrainAtlasEdge[];
};

export type MemoryActivityAgent = {
  agent: AgentId;
  retrievals: number;
  hits: number;
  misses: number;
  selected: number;
  used: number;
  crossAgentUsed: number;
  lastRetrievalAt: string | null;
  lastSelectedAt: string | null;
  lastUsedAt: string | null;
  lastCrossAgentUsedAt: string | null;
};

export type MemoryReuseLink = {
  sourceAgent: AgentId;
  consumerAgent: AgentId;
  uses: number;
  lastUsedAt: string;
};

export type MemoryActivity = {
  schemaVersion: 2;
  generatedAt: string;
  windowMinutes: number;
  motionWindowSeconds: number;
  source: { name: "governed-memory-registry"; verified: boolean };
  privacy: {
    queryIncluded: false;
    contentIncluded: false;
    rawIdentifiersIncluded: false;
    reasonsIncluded: false;
    countsOnly: true;
  };
  counts: {
    retrievals: number;
    hits: number;
    misses: number;
    selected: number;
    used: number;
    crossAgentUsed: number;
    reuseIgnored: number;
    feedback: number;
    helpful: number;
    feedbackIgnored: number;
    corrected: number;
    harmful: number;
    proposed: number;
    promoted: number;
  };
  lastObservedAt: {
    retrieval: string | null;
    hit: string | null;
    miss: string | null;
    selected: string | null;
    used: string | null;
    crossAgentUsed: string | null;
    reuseIgnored: string | null;
    feedback: string | null;
    corrected: string | null;
    proposed: string | null;
    promoted: string | null;
  };
  agents: MemoryActivityAgent[];
  reuseLinks: MemoryReuseLink[];
};

export type MemoryOperations = Record<string, unknown> & {
  updatedAt?: string;
  status?: string;
  activity?: MemoryActivity;
};

export type MissionControlState = {
  source: string;
  statuses: AgentStatus[];
  events: AgentEvent[];
  jobs: AgentJob[];
  todayJobs?: TodayJobOccurrence[];
  todayJobsMeta?: TodayJobsMeta;
  workHot?: ControlTowerHot;
  activeModelRoutes?: ActiveModelRoute[];
  approvals: Approval[];
  operationalAlerts: OperationalAlert[];
  agenticCrypto?: AgenticCryptoWallet;
  modelUsage?: ModelUsage;
  modelRouter?: ModelRouter;
  qualityControl?: Record<string, unknown>;
  maintenanceControl?: Record<string, unknown>;
  reliabilityUpgrades?: ReliabilityUpgrades;
  brainAtlas?: BrainAtlas;
  capabilityStack?: CapabilityStackItem[];
  capabilityInventory?: CapabilityInventory;
  capabilityWatch?: CapabilityWatch;
  signalHealth?: SignalHealth;
  machineHealth?: Record<string, unknown>;
  runtimeLayout?: Record<string, unknown>;
  sharedOperatingLayer?: Record<string, unknown>;
  agentControl?: Record<string, unknown>;
  agentContextRegistry?: Record<string, unknown>;
  memoryOperations?: MemoryOperations;
  codingVisibility?: Record<string, unknown>;
  trackedTasks?: Array<Record<string, unknown>>;
  agentBus?: Array<Record<string, unknown>>;
  signals: SignalItem[];
};

export type AgenticCryptoWallet = {
  updatedAt?: string;
  status?: "fresh" | "stale" | "error" | string;
  walletMode?: "read-only" | "simulation-ready" | "approval-required" | "execution-enabled" | string;
  refreshMode?: string;
  wallets?: {
    evmMasked?: string;
    solanaMasked?: string;
  };
  summary?: {
    totalEstimatedUsd?: number;
    liquidEstimatedUsd?: number;
    nftEstimatedUsd?: number;
    lastRefreshed?: string;
    freshnessStatus?: string;
  };
  tradingGoal?: {
    title?: string;
    description?: string;
    current?: number;
    target?: number;
    unit?: string;
    status?: string;
    updatedAt?: string;
  };
  chains?: Array<{
    chain: string;
    gasSymbol: string;
    gasBalance?: number;
    gasValueUsd?: number;
    gasStatus?: "ready" | "low" | "empty" | "unknown" | string;
    estimatedGasBudgetUsd?: number;
  }>;
  tokens?: Array<{
    chain: string;
    symbol: string;
    name?: string;
    amount?: number;
    valueUsd?: number;
    source?: string;
    priceSource?: string;
    contractMasked?: string;
    mintMasked?: string;
    classification?: "core" | "useful" | "speculative" | "dust" | "unknown" | string;
  }>;
  nfts?: Array<{
    chain: string;
    collection: string;
    tokenStandard?: string;
    count?: number;
    floorUsd?: number | null;
    source?: string;
    confidence?: "high" | "medium" | "low" | "unavailable" | string;
  }>;
  approvals?: Array<{
    chain: string;
    token: string;
    spenderMasked?: string;
    spenderLabel?: string;
    allowanceType?: "finite" | "unlimited-like" | string;
    risk?: "low" | "attention" | "revoke recommended" | string;
    lastApprovalAt?: string;
  }>;
  recentActivity?: Array<{
    timestamp?: string;
    action?: string;
    status?: string;
    chain?: string;
    valueSummary?: string;
    explorerLabel?: string;
    explorerUrl?: string;
  }>;
  tradeLedger?: Array<{
    timestamp?: string;
    side?: "open" | "close" | "swap" | "rebalance" | "approve" | string;
    action?: string;
    asset?: string;
    pair?: string;
    amount?: string | number;
    valueUsd?: number;
    pnl?: string | number | null;
    pnlUsd?: number | null;
    pnlSol?: number | null;
    status?: string;
    chain?: string;
    explorerLabel?: string;
    explorerUrl?: string;
  }>;
  opportunities?: Array<{
    actionType: string;
    chain?: string;
    estimatedCost?: string;
    expectedBenefit?: string;
    riskLevel?: "low" | "medium" | "high" | string;
    simulationStatus?: string;
    requiredApproval?: string;
  }>;
  guardrails?: {
    chainAllowlist?: string[];
    dailyGasCapUsd?: number;
    maxTransactionValueUsd?: number;
    maxApprovalValueUsd?: number;
    simulationRequired?: boolean;
    blockUnlimitedApprovals?: boolean;
    blockSetApprovalForAll?: boolean;
    swapsRequireApproval?: boolean;
    bridgingRequiresApproval?: boolean;
    stakingRequiresApproval?: boolean;
    mintingRequiresApproval?: boolean;
    unknownContractWritesBlocked?: boolean;
  };
  errors?: string[];
};

export type CapabilityStackItem = {
  id: string;
  name: string;
  status: string;
  summary?: string;
  detail?: string;
};

export type CapabilityInventory = {
  updatedAt?: string;
  nodes?: Array<Record<string, unknown>>;
};

export type CapabilityWatch = {
  updatedAt?: string;
  checkedAt?: string;
  status?: string;
  summary?: string;
  recommendations?: Array<Record<string, unknown>>;
};

export type ReliabilityUpgradeItem = {
  id: string;
  label: string;
  owner: string;
  status: string;
  signal: string;
  whyItMatters: string;
  evidence: string;
  next: string;
};

export type ReliabilityUpgradeMetric = {
  label: string;
  value: string | number;
  status?: string;
  detail?: string;
};

export type ReliabilityUpgrades = {
  updatedAt?: string;
  summary?: string;
  items: ReliabilityUpgradeItem[];
  metrics?: ReliabilityUpgradeMetric[];
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
  aggregate?: {
    daily?: number;
    total?: number;
    monthly?: number;
  };
  jain?: {
    daily?: number;
    session?: number;
    total?: number;
    available?: boolean;
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
  xai?: {
    daily?: number;
    weekly?: number;
    monthly?: number;
    callsToday?: number;
    callsWeekly?: number;
    okToday?: number;
    failedToday?: number;
    inputTokens?: number;
    outputTokens?: number;
    totalTokens?: number;
    outputChars?: number;
    sourceCount?: number;
    lastModel?: string;
    lastStatus?: string;
    lastRunAt?: string;
    available?: boolean;
  };
  providerBreakdown?: ProviderBudget[];
  providerBudgets?: ProviderBudget[];
  codexbarLimits?: Record<string, Partial<ProviderBudget> & { available?: boolean }>;
  routerPolicy?: Record<string, unknown>;
};

export type ProviderBudget = {
  id: string;
  label: string;
  role?: string;
  budgetType?: string;
  plan?: string;
  subscriptionMonthlyUsd?: number;
  subscriptionAnnualUsd?: number;
  subscriptionCadence?: string;
  billingLabel?: string;
  billingNote?: string;
  overagePolicy?: string;
  requiresApiKey?: boolean;
  monthlyCapUsd?: number;
  dailyCapUsd?: number;
  reserveUsd?: number;
  remainingCreditUsd?: number | null;
  dailySpendUsd?: number;
  weeklySpendUsd?: number;
  monthlySpendUsd?: number;
  dailyUtilizationPct?: number;
  monthlyUtilizationPct?: number;
  status?: string;
  authStatus?: string;
  keyPresent?: boolean | null;
  keySuffix?: string;
  lastTestStatus?: string;
  lastModelUsed?: string;
  whyChosen?: string;
  accountLabel?: string;
  codexbarSource?: string;
  codexbarUpdatedAt?: string;
  usagePct?: number;
  summary?: string;
  usageSummary?: string;
  fixedMonthlyUsd?: number;
  meteredDailyUsd?: number;
  meteredWeeklyUsd?: number;
  meteredMonthlyUsd?: number;
  usageEquivalentDailyUsd?: number;
  usageEquivalentWeeklyUsd?: number;
  usageEquivalentMonthlyUsd?: number;
  callsToday?: number;
  callsWeekly?: number;
  sessions?: number;
  totalTokens?: number;
  inputTokens?: number;
  outputTokens?: number;
  topModels?: Array<{
    name: string;
    source?: string;
    weeklyCost?: number;
    dailyCost?: number;
    usageEquivalentCost?: number;
    marginalCost?: number;
    sessions?: number;
    callsWeekly?: number;
    totalTokens?: number;
  }>;
  usageWindows?: Array<{
    id?: string;
    label: string;
    usedPercent?: number;
    remainingPercent?: number;
    resetDescription?: string;
    resetsAt?: string;
    windowMinutes?: number;
    status?: string;
    remainingLabel?: string;
  }>;
};

export type ModelRouter = {
  updatedAt?: string;
  summary?: string;
  codexAllowanceMode?: string;
  policy?: Record<string, unknown>;
  providers?: ProviderBudget[];
  guardrails?: string[];
  ladder?: Array<Record<string, unknown>>;
  ladderStatus?: string;
  routeQualityScore?: number | null;
  efficiencyScore?: number | null;
  routeMix?: Record<string, number>;
  routeAlerts?: string[];
  lastRoute?: Record<string, unknown>;
};

export type SignalItem = {
  id: string;
  label: string;
  title: string;
  reason: string;
  impact?: string;
  impactScenarios?: {
    low?: string;
    medium?: string;
    med?: string;
    high?: string;
  };
  kind?: string;
  source: string;
  score?: number;
  time?: string;
  url?: string;
  section?: string;
  sectionLabel?: string;
  rank?: number;
};

export type SignalHealth = {
  generatedAt?: string;
  status?: string;
  summary?: string;
  agesMinutes?: Record<string, number | null>;
  counts?: {
    live?: number;
    newsletter?: number;
    total?: number;
    filteredLowQuality?: number;
    breakingSourceItems?: number;
    newsfeedSourceItems?: number;
    newsletterTrendItems?: number;
  };
  topSources?: Array<{ source?: string; count?: number }>;
  staleSources?: string[];
  qualityPolicy?: string;
};

declare global {
  interface Window {}
}

export type AgentId = "joshex" | "josh2" | "jaimes" | "jain";

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
  schedule?: string;
  sourceLabel?: string;
  runStatus?: string;
  lastRun?: string;
  nextRun?: string;
  verifiedToday?: boolean;
  todayRelevant?: boolean;
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
  agenticCrypto?: AgenticCryptoWallet;
  modelUsage?: ModelUsage;
  modelRouter?: ModelRouter;
  reliabilityUpgrades?: ReliabilityUpgrades;
  capabilityStack?: CapabilityStackItem[];
  capabilityInventory?: CapabilityInventory;
  capabilityWatch?: CapabilityWatch;
  signalHealth?: SignalHealth;
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
  accountEmail?: string;
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

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
  lastUpdated?: string;
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
  runtimeLayout?: RuntimeLayoutHealth;
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
  opportunities?: Array<{
    actionType: string;
    chain?: string;
    estimatedCost?: string;
    expectedBenefit?: string;
    riskLevel?: "low" | "medium" | "high" | string;
    simulationStatus?: string;
    requiredApproval?: string;
  }>;
  baseMcp?: {
    status?: "not-connected" | "read-only-ready" | "proposal-ready" | "approval-required" | "error" | string;
    mode?: "read-only" | "proposal-only" | "approval-required" | "disabled" | string;
    accountConnection?: "not-connected" | "connected" | "pending-user-approval" | string;
    lastChecked?: string;
    summary?: string;
    owner?: string;
    capabilities?: string[];
    guardrails?: string[];
    pendingProposals?: Array<{
      id?: string;
      title: string;
      status?: "draft" | "needs-review" | "approved" | "rejected" | "expired" | string;
      chain?: string;
      risk?: "low" | "medium" | "high" | string;
      next?: string;
    }>;
    links?: Array<{
      label: string;
      url: string;
    }>;
  };
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

export type RuntimeLayoutHealth = {
  ok?: boolean;
  status?: string;
  checkedAt?: string;
  summary?: string;
  title?: string;
  url?: string;
  viewport?: {
    width?: number;
    height?: number;
    scrollWidth?: number;
    scrollHeight?: number;
  };
  visibleCounts?: {
    agentCards?: number;
    signalRows?: number;
    calendarBlocks?: number;
    tokenRows?: number;
  };
  issues?: string[];
  target?: {
    title?: string;
    url?: string;
    idHash?: string;
  };
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

export type ModelProviderBreakdown = {
  id: string;
  label: string;
  budgetLabel?: string;
  budgetType?: string;
  monthlyFeeUsd?: number;
  fixedMonthlyUsd?: number;
  meteredDailyUsd?: number;
  meteredWeeklyUsd?: number;
  meteredMonthlyUsd?: number;
  usageEquivalentDailyUsd?: number;
  usageEquivalentWeeklyUsd?: number;
  usageEquivalentMonthlyUsd?: number;
  usagePct?: number;
  usageSummary?: string;
  inferredFullCallEquivalent?: number;
  inferredRemainingCallEquivalent?: number;
  callsToday?: number;
  callsWeekly?: number;
  sessions?: number;
  totalTokens?: number;
  inputTokens?: number;
  outputTokens?: number;
  summary?: string;
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
  providerBreakdown?: ModelProviderBreakdown[];
  providerBudgets?: ProviderBudget[];
  routerPolicy?: Record<string, unknown>;
};

export type ProviderBudget = {
  id: string;
  label: string;
  role?: string;
  budgetType?: string;
  monthlyFeeUsd?: number;
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
  usageProbeSummary?: string;
  subscriptionLabel?: string;
  subscriptionCreditPct?: number;
  whyChosen?: string;
  fixedMonthlyUsd?: number;
  fixedWeeklyUsd?: number;
  allowanceLabel?: string;
  usageAllowance?: string;
  displayWhenIdle?: boolean;
};

export type ModelRouter = {
  updatedAt?: string;
  summary?: string;
  codexAllowanceMode?: string;
  policy?: Record<string, unknown>;
  providers?: ProviderBudget[];
  guardrails?: string[];
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
  quietHours?: boolean;
  nextBreakingRun?: string;
  agesMinutes?: Record<string, number | null>;
  counts?: {
    live?: number;
    newsletter?: number;
    total?: number;
    filteredLowQuality?: number;
    breakingSourceItems?: number;
    newsfeedSourceItems?: number;
    newsletterTrendItems?: number;
    publicRssFallbackItems?: number;
  };
  topSources?: Array<{ source?: string; count?: number }>;
  staleSources?: string[];
  coveredStaleSources?: string[];
  fallbackFresh?: boolean;
  qualityPolicy?: string;
};

declare global {
  interface Window {
    MC_V2_CONFIG?: {
      supabaseUrl?: string;
      supabaseKey?: string;
      supabaseMode?: "primary" | "optional" | "disabled";
    };
  }
}

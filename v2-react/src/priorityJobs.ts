export type PriorityJobKey = "gmail" | "sorare" | "fantasy";

export type PriorityJobRule = {
  key: PriorityJobKey;
  label: string;
  agent: string;
  pattern: RegExp;
};

export type SorareGroupKey = "lineups" | "missions" | "general";

export type SorareDailyGroup = {
  key: SorareGroupKey;
  label: string;
  pattern: RegExp;
};

export const PRIORITY_JOB_RULES: PriorityJobRule[] = [
  {
    key: "gmail",
    label: "Personal Gmail Triage",
    agent: "JOSHeX",
    pattern: /personal gmail|gmail morning|gmail inbox|gmail triage|email triage|mail triage|inbox triage|inbox review|unread email/,
  },
  {
    key: "sorare",
    label: "Sorare",
    agent: "JAIMES",
    pattern: /sorare/,
  },
  {
    key: "fantasy",
    label: "Fantasy Baseball",
    agent: "JAIMES",
    pattern: /fantasy|waiver|roster|lineup|pitcher|baseball/,
  },
];

export const SORARE_DAILY_GROUPS: SorareDailyGroup[] = [
  {
    key: "lineups",
    label: "GW Limited Lineup submissions",
    pattern: /lineup|lineups|gw|game-week|draft report|pre-lock|rp|champion|challenger|hot streak|deadline|lineup submit|sp classic/,
  },
  {
    key: "missions",
    label: "Daily Missions",
    pattern: /daily mission|missions|mission picks|claim|prep|daily pick|reward/,
  },
  {
    key: "general",
    label: "General",
    pattern: /sorare|gw12|training|model|edge|outcome|calibrator|sheet|data|tracker|canonical|reflector|sync|auth|cookie|login|credential/,
  },
];

export const SORARE_GENERAL_PATTERN = /auth|cookie|login|credential|training|model|edge|outcome|calibrator|sheet|data|tracker|canonical|reflector|sync/;

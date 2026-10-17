/**
 * Improvement-loop management — runtime config, schedule, runs (Review-process panel).
 *
 *   GET   /api/v1/loop-config         → {effective, env_default, recent_runs, recent_requests}
 *   PATCH /api/v1/loop-config         → updated LoopConfigRead
 *   POST  /api/v1/loop-config/run     → LoopRunRequest (enqueue a manual run, or a single turn)
 *   GET   /api/v1/loop-runs/{id}      → LoopRunDetail (with report markdown)
 *   GET   /api/v1/loop-run-requests/{id} → LoopRunRequest (poll one request to completion)
 *   GET   /api/v1/loop-findings?turn_id= → the finding for one turn
 *   GET   /api/v1/loop-run-requests/queue → LoopQueueStatus (is the worker draining?)
 *   GET   /api/v1/loop-fix-checks     → did a claimed fix hold?
 *   POST  /api/v1/golden-tests        → GoldenTest (add a regression case by hand)
 */

import { createMutation, createQuery, useQueryClient } from '@tanstack/svelte-query';
import { z } from 'zod';
import { api } from '$lib/api/client';
import { CallParamsSchema, templateKeys } from '$lib/features/llm-config/templates';

export const LoopConfigValuesSchema = z.object({
	schedule_enabled: z.boolean(),
	schedule_hour: z.number().int(),
	schedule_minute: z.number().int(),
	lookback_hours: z.number().int(),
	max_conversations: z.number().int(),
	concurrency: z.number().int(),
	triage_mode: z.enum(['model', 'all', 'feedback_only']).catch('model'),
	triage_model: z.string(),
	triage_max_tokens: z.number().int(),
	// dual = two independent analysts + a reconciling judge; single = one analyst +
	// a critic that re-checks it. A `dual` config with no distinct model B degrades
	// to single at run time — see `panel` on LoopConfigRead.
	analysis_mode: z.enum(['dual', 'single']).catch('dual'),
	analysis_model_a: z.string(),
	analysis_a_max_tokens: z.number().int(),
	analysis_model_b: z.string(),
	analysis_b_max_tokens: z.number().int(),
	judge_model: z.string(),
	judge_max_tokens: z.number().int(),
	remediation_model: z.string(),
	remediation_max_tokens: z.number().int(),
	// Extended-thinking budget for every review stage (0 = off).
	thinking_budget_tokens: z.number().int().default(0),
	evidence_rejected_max: z.number().int(),
	evidence_rejected_chars: z.number().int(),
	evidence_max_context_chars: z.number().int(),
	novelty_covered_threshold: z.number(),
	novelty_gap_threshold: z.number(),
	grounding_threshold: z.number(),
	entailment_required: z.boolean(),
	cluster_enabled: z.boolean(),
	cluster_threshold: z.number(),
	verification_enabled: z.boolean(),
	verification_max_cases: z.number().int(),
	auto_insert_enabled: z.boolean(),
	auto_insert_min_confidence: z.number(),
	curate_web_search_gaps: z.boolean(),
	meta_retire_enabled: z.boolean(),
	// LLM template per stage ('' → the legacy model/max-tokens fields above).
	triage_template: z.string().default(''),
	analysis_a_template: z.string().default(''),
	analysis_b_template: z.string().default(''),
	judge_template: z.string().default(''),
	remediation_template: z.string().default('')
});
export type LoopConfigValues = z.infer<typeof LoopConfigValuesSchema>;

/** What the configured panel will ACTUALLY do next run (degradation made visible). */
export const LoopPanelSchema = z.object({
	mode_requested: z.string(),
	mode_effective: z.string(),
	degraded_reason: z.string().default(''),
	judge_independent: z.boolean().default(true),
	models: z.record(z.string(), z.string()).default({})
});
export type LoopPanel = z.infer<typeof LoopPanelSchema>;

const CandidateKnowledgeSchema = z
	.object({
		topic: z.string().nullish(),
		claim: z.string().nullish(),
		required_citations: z.array(z.string()).nullish(),
		currency_basis: z.string().nullish(),
		stakes: z.string().nullish()
	})
	.passthrough();

// One analysis model's verdict (Analyse A / B) — mirrors TurnAnalysis.
export const TurnAnalysisSchema = z
	.object({
		user_information_need: z.string().nullish(),
		answer_assessment: z.string().nullish(),
		failure_mode: z.string().nullish(),
		evidence: z.string().nullish(),
		feedback_alignment: z.string().nullish(),
		candidate_knowledge: CandidateKnowledgeSchema.nullish(),
		confidence: z.number().nullish()
	})
	.passthrough();

export const TriageDecisionSchema = z
	.object({
		mode: z.string().nullish(),
		analyze: z.boolean().nullish(),
		reason: z.string().nullish()
	})
	.passthrough();

// The judge's reconciliation — mirrors JudgedCandidate.
export const JudgeResultSchema = z
	.object({
		answer_assessment: z.string().nullish(),
		failure_mode: z.string().nullish(),
		agreement: z.string().nullish(),
		synthesis_confidence: z.number().nullish(),
		candidate_knowledge: CandidateKnowledgeSchema.nullish(),
		rationale: z.string().nullish(),
		conflict_note: z.string().nullish()
	})
	.passthrough();

/** The fix artifacts: what the answer should have been, and how to prevent it. */
const RemediationSchema = z
	.object({
		corrected_answer: z.string().nullish(),
		checkable_facts: z.array(z.string()).nullish(),
		forbidden_facts: z.array(z.string()).nullish(),
		citations: z.array(z.string()).nullish(),
		directive_proposal: z
			.object({
				title: z.string().nullish(),
				// An imperative rule, not the diagnosis — this is what the
				// "Create directive" action pre-fills.
				rule: z.string().nullish(),
				applies_when: z.string().nullish()
			})
			.passthrough()
			.nullish(),
		remediation_kind: z.string().nullish(),
		confidence: z.number().nullish(),
		rationale: z.string().nullish()
	})
	.passthrough();

export const LoopFindingSchema = z.object({
	id: z.number().int(),
	run_id: z.number().int().nullable(),
	turn_id: z.string(),
	// The finding's own agent. Remediation actions must target THIS, not the admin
	// scope selector — that is blank in global mode and would default to `default`.
	profile_id: z.string().nullable().default(null),
	session_id: z.string().nullable(),
	user_id: z.string().nullable(),
	assessment: z.string().nullable(),
	failure_mode: z.string().nullable(),
	route: z.string().nullable(),
	novelty: z.string().nullable(),
	synthesis_confidence: z.number().nullable(),
	evidence: z.string().nullable(),
	created_at: z.string(),
	question: z.string().nullable(),
	answer: z.string().nullable(),
	// Per-stage detail for the detail view (null on pre-feature rows / failed model).
	triage: TriageDecisionSchema.nullable().default(null),
	analysis_a: TurnAnalysisSchema.nullable().default(null),
	analysis_b: TurnAnalysisSchema.nullable().default(null),
	judge: JudgeResultSchema.nullable().default(null),
	// Did the analysis read the retrieved sources, or judge from the transcript
	// alone? `degraded` findings are guesses — never auto-inserted, and bucketed
	// separately in the dismissal-rate stats.
	evidence_completeness: z.string().default('degraded'),
	// The fix artifacts. Every field defaults so pre-migration rows still parse.
	remediation: RemediationSchema.nullable().default(null),
	remediation_kind: z.string().nullable().default(null),
	// Reviewer verdict on the FINDING itself (null = not yet reviewed).
	disposition: z.string().nullable().default(null),
	disposition_reason: z.string().nullable().default(null),
	reviewed_by: z.string().nullable().default(null),
	reviewed_at: z.string().nullable().default(null),
	cluster_id: z.number().int().nullable().default(null),
	// Linked KB candidate (content-gap turns awaiting human confirmation).
	candidate_id: z.number().int().nullable().default(null),
	candidate_status: z.string().nullable().default(null),
	needs_confirmation: z.boolean().default(false)
});
export type LoopFinding = z.infer<typeof LoopFindingSchema>;
export type Remediation = z.infer<typeof RemediationSchema>;
export type TurnAnalysis = z.infer<typeof TurnAnalysisSchema>;
export const LoopFindingListSchema = z.array(LoopFindingSchema);

export const LoopRunSummarySchema = z.object({
	id: z.number().int(),
	run_date: z.string(),
	status: z.string(),
	dry_run: z.boolean(),
	started_at: z.string().nullable(),
	finished_at: z.string().nullable(),
	error: z.string().nullable(),
	stats: z.record(z.string(), z.unknown()).default({})
});
export type LoopRunSummary = z.infer<typeof LoopRunSummarySchema>;

export const LoopRunDetailSchema = LoopRunSummarySchema.extend({
	report_md: z.string().nullable()
});
export type LoopRunDetail = z.infer<typeof LoopRunDetailSchema>;

export const LoopRunRequestSchema = z.object({
	id: z.number().int(),
	status: z.string(),
	trigger: z.string(),
	dry_run: z.boolean(),
	force: z.boolean().default(false),
	run_date: z.string().nullable(),
	requested_by: z.string().nullable(),
	requested_at: z.string(),
	started_at: z.string().nullable(),
	finished_at: z.string().nullable(),
	run_id: z.number().int().nullable(),
	error: z.string().nullable(),
	// Set → this request is a single-turn review rather than a window run.
	turn_id: z.string().nullable().default(null),
	include_dismissed: z.boolean().default(false),
	// An admin asked a running request to stop; it stays `running` until the
	// worker reaches its next safe point and finishes it as `cancelled`.
	cancel_requested: z.boolean().default(false)
});
export type LoopRunRequest = z.infer<typeof LoopRunRequestSchema>;

/** Terminal request statuses — polling stops here. */
export const REQUEST_SETTLED = new Set(['done', 'failed', 'cancelled']);
export const isRequestSettled = (status: string | undefined) =>
	status !== undefined && REQUEST_SETTLED.has(status);

/** One re-ask of a golden test through the live agent — did the fix hold? */
export const LoopFixCheckSchema = z.object({
	id: z.number().int(),
	profile_id: z.string().nullable().default(null),
	golden_test_id: z.number().int(),
	run_id: z.number().int().nullable().default(null),
	checked_at: z.string(),
	passed: z.boolean(),
	answer: z.string().nullable().default(null),
	missing_facts: z.array(z.string()).default([]),
	forbidden_hits: z.array(z.string()).default([]),
	error: z.string().nullable().default(null),
	question: z.string().nullable().default(null),
	kind: z.string().nullable().default(null),
	source_turn_id: z.string().nullable().default(null)
});
export type LoopFixCheck = z.infer<typeof LoopFixCheckSchema>;

/** Is the worker draining the queue? */
export const LoopQueueStatusSchema = z.object({
	requested: z.number().int().default(0),
	oldest_requested_age_seconds: z.number().int().nullable().default(null),
	running: z.number().int().default(0),
	running_since: z.string().nullable().default(null),
	stale: z.boolean().default(false),
	waiting_too_long: z.boolean().default(false)
});
export type LoopQueueStatus = z.infer<typeof LoopQueueStatusSchema>;

export const GoldenTestSchema = z.object({
	id: z.number().int(),
	run_id: z.number().int().nullable(),
	source_turn_id: z.string(),
	question: z.string(),
	expected: z.string().nullable(),
	// What the eval actually asserts. `expected` is reference prose (and, for a
	// good_answer, the REDACTED transcript) so it can't be substring-matched.
	expected_facts: z.array(z.string()).default([]),
	forbidden_facts: z.array(z.string()).default([]),
	citations: z.array(z.string()).default([]),
	kind: z.string(),
	legal_year: z.number().int().nullable(),
	// Set once a reviewer hand-edits the expectation; the loop then stops
	// overwriting this row on its nightly run.
	edited_by: z.string().nullable().default(null),
	created_at: z.string()
});
export type GoldenTest = z.infer<typeof GoldenTestSchema>;

/** A recurring problem — the unit a reviewer acts on, above individual findings. */
export const LoopClusterSchema = z.object({
	id: z.number().int(),
	profile_id: z.string().nullable().default(null),
	route: z.string(),
	title: z.string(),
	summary: z.string().nullable().default(null),
	recommended_action: z.string().nullable().default(null),
	finding_count: z.number().int().default(0),
	first_run_id: z.number().int().nullable().default(null),
	last_run_id: z.number().int().nullable().default(null),
	// open → ticketed → resolved | dismissed. The row IS the ticket.
	status: z.string(),
	ticket_source_id: z.string().nullable().default(null),
	reviewer: z.string().nullable().default(null),
	review_notes: z.string().nullable().default(null),
	created_at: z.string(),
	updated_at: z.string()
});
export type LoopCluster = z.infer<typeof LoopClusterSchema>;

/** Dismissal rate — the evidence that an analysis change actually helped. */
export const LoopFindingStatsSchema = z.object({
	total: z.number().int().default(0),
	reviewed: z.number().int().default(0),
	dismissed: z.number().int().default(0),
	dismissal_rate: z.number().nullable().default(null),
	by_bucket: z
		.array(
			z.object({
				failure_mode: z.string().nullable().default(null),
				evidence_completeness: z.string().nullable().default(null),
				total: z.number().int().default(0),
				reviewed: z.number().int().default(0),
				dismissed: z.number().int().default(0),
				useful: z.number().int().default(0),
				dismissal_rate: z.number().nullable().default(null)
			})
		)
		.default([])
});
export type LoopFindingStats = z.infer<typeof LoopFindingStatsSchema>;

export const LoopConfigReadSchema = z.object({
	effective: LoopConfigValuesSchema,
	env_default: LoopConfigValuesSchema,
	available_models: z.array(z.string()).default([]),
	recent_runs: z.array(LoopRunSummarySchema),
	recent_requests: z.array(LoopRunRequestSchema),
	// Nullable: an older backend won't send it, and blanking the whole panel over a
	// missing advisory field would be worse than not showing the warning.
	panel: LoopPanelSchema.nullable().default(null),
	// What each review stage will send ({stage: call params}).
	stage_params: z.record(CallParamsSchema).default({})
});
export type LoopConfigRead = z.infer<typeof LoopConfigReadSchema>;

export const loopKeys = {
	all: ['loop-config'] as const,
	config: () => [...loopKeys.all, 'config'] as const,
	findings: () => [...loopKeys.all, 'findings'] as const,
	findingByTurn: (turnId: string) => [...loopKeys.all, 'finding-by-turn', turnId] as const,
	request: (id: number) => [...loopKeys.all, 'request', id] as const,
	goldenTests: () => [...loopKeys.all, 'golden-tests'] as const,
	fixChecks: () => [...loopKeys.all, 'fix-checks'] as const,
	queue: () => [...loopKeys.all, 'queue'] as const,
	clusters: () => [...loopKeys.all, 'clusters'] as const,
	stats: () => [...loopKeys.all, 'stats'] as const
};

/** Server-side findings filters. Every one is applied in SQL — a client-side
 *  filter over a capped page could hide hundreds of matching rows. */
export interface FindingsFilters {
	route?: string | null;
	failure_mode?: string | null;
	assessment?: string | null;
	/** `unreviewed` = not yet given a verdict. */
	disposition?: string | null;
	run_id?: number | null;
	cluster_id?: number | null;
	limit?: number;
	offset?: number;
}

function findingsPath(filters: FindingsFilters, profileId?: string | null): string {
	const qs = new URLSearchParams();
	qs.set('limit', String(filters.limit ?? 50));
	if (filters.offset) qs.set('offset', String(filters.offset));
	for (const key of ['route', 'failure_mode', 'assessment', 'disposition'] as const) {
		const v = filters[key];
		if (v && v !== 'all') qs.set(key, v);
	}
	if (filters.run_id != null) qs.set('run_id', String(filters.run_id));
	if (filters.cluster_id != null) qs.set('cluster_id', String(filters.cluster_id));
	return lp(`/v1/loop-findings?${qs.toString()}`, profileId);
}

const lp = (path: string, profileId?: string | null) => {
	const sep = path.includes('?') ? '&' : '?';
	return profileId ? `${path}${sep}profile_id=${encodeURIComponent(profileId)}` : path;
};

export function createFindingsQuery(
	initialData?: LoopFinding[],
	refetchInterval = 8000,
	profileId?: string | null,
	filters: FindingsFilters = { limit: 200 }
) {
	return createQuery({
		queryKey: [...loopKeys.findings(), profileId ?? '__global__', filters],
		queryFn: async () =>
			LoopFindingListSchema.parse(await api.get<unknown>(findingsPath(filters, profileId))),
		initialData,
		refetchInterval
	});
}

/** Whether the worker is draining the queue — shown as a banner when it is not. */
export function createQueueStatusQuery(profileId?: string | null, refetchInterval = 10000) {
	return createQuery({
		queryKey: [...loopKeys.queue(), profileId ?? '__global__'],
		queryFn: async () =>
			LoopQueueStatusSchema.parse(
				await api.get<unknown>(lp('/v1/loop-run-requests/queue', profileId))
			),
		refetchInterval
	});
}

export function createFixChecksQuery(profileId?: string | null, limit = 100) {
	return createQuery({
		queryKey: [...loopKeys.fixChecks(), profileId ?? '__global__', limit],
		queryFn: async () =>
			z
				.array(LoopFixCheckSchema)
				.parse(await api.get<unknown>(lp(`/v1/loop-fix-checks?limit=${limit}`, profileId))),
		refetchInterval: 30000
	});
}

export function createLoopConfigQuery(
	initialData?: LoopConfigRead,
	refetchInterval = 5000,
	profileId?: string | null
) {
	return createQuery({
		queryKey: [...loopKeys.config(), profileId ?? '__global__'],
		queryFn: async () =>
			LoopConfigReadSchema.parse(await api.get<unknown>(lp('/v1/loop-config', profileId))),
		initialData,
		refetchInterval
	});
}

export function updateLoopConfigMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (patch: Partial<LoopConfigValues>) =>
			LoopConfigReadSchema.parse(await api.patch<unknown>(lp('/v1/loop-config', profileId), patch)),
		onSuccess: (data) => {
			qc.setQueryData([...loopKeys.config(), profileId ?? '__global__'], data);
			void qc.invalidateQueries({ queryKey: templateKeys.all });
		}
	});
}

/**
 * Stop a review. A queued request is cancelled outright; a running one is
 * flagged and the worker abandons it at its next safe point (the findings it
 * already produced are kept), finishing it as `cancelled`.
 */
export function cancelRunRequestMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (id: number) =>
			LoopRunRequestSchema.parse(await api.post<unknown>(`/v1/loop-run-requests/${id}/cancel`, {})),
		onSuccess: (data) => {
			qc.setQueryData(loopKeys.request(data.id), data);
			qc.invalidateQueries({ queryKey: loopKeys.config() });
			qc.invalidateQueries({ queryKey: loopKeys.queue() });
		}
	});
}

export function enqueueRunMutation(profileId?: string | null) {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (body: {
			dry_run: boolean;
			force?: boolean;
			run_date?: string | null;
			// Re-analyse turns a reviewer dismissed. `force` alone deliberately does
			// NOT resurrect them — that is the point of a dismissal.
			include_dismissed?: boolean;
		}) =>
			LoopRunRequestSchema.parse(
				await api.post<unknown>('/v1/loop-config/run', { ...body, profile_id: profileId ?? null })
			),
		onSuccess: () => qc.invalidateQueries({ queryKey: loopKeys.config() })
	});
}

/** Recurring problems, largest first — the decision surface above findings. */
export function createClustersQuery(
	profileId?: string | null,
	status: string | null = 'open',
	refetchInterval = 15000
) {
	const qs = new URLSearchParams({ limit: '200' });
	if (status) qs.set('status', status);
	return createQuery({
		queryKey: [...loopKeys.clusters(), profileId ?? '__global__', status ?? 'all'],
		queryFn: async () =>
			z
				.array(LoopClusterSchema)
				.parse(await api.get<unknown>(lp(`/v1/loop-clusters?${qs.toString()}`, profileId))),
		refetchInterval
	});
}

export function updateClusterMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({
			id,
			...patch
		}: {
			id: number;
			status?: 'open' | 'ticketed' | 'resolved' | 'dismissed';
			review_notes?: string;
			ticket_source_id?: string;
		}) => LoopClusterSchema.parse(await api.patch<unknown>(`/v1/loop-clusters/${id}`, patch)),
		onSuccess: () => qc.invalidateQueries({ queryKey: loopKeys.clusters() })
	});
}

/**
 * Record a reviewer's verdict on a finding.
 *
 * `dismissed` also excludes the turn from future runs (including forced re-runs),
 * so the same false positive stops resurfacing every night. Passing
 * `disposition: null` clears the verdict and puts the turn back in scope.
 */
export function updateFindingDispositionMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({
			id,
			...patch
		}: {
			id: number;
			disposition: 'useful' | 'dismissed' | 'duplicate' | 'fixed' | null;
			disposition_reason?: string | null;
		}) => LoopFindingSchema.parse(await api.patch<unknown>(`/v1/loop-findings/${id}`, patch)),
		onSuccess: () => {
			qc.invalidateQueries({ queryKey: loopKeys.findings() });
			qc.invalidateQueries({ queryKey: loopKeys.stats() });
			// The per-turn review panel in Conversations reads its own key; without
			// this it kept showing the verdict from before the disposition.
			qc.invalidateQueries({ queryKey: [...loopKeys.all, 'finding-by-turn'] });
		}
	});
}

/** Dismissal rate by failure mode × evidence completeness — did the loop improve? */
export function createFindingsStatsQuery(profileId?: string | null) {
	return createQuery({
		queryKey: [...loopKeys.stats(), profileId ?? '__global__'],
		queryFn: async () =>
			LoopFindingStatsSchema.parse(
				await api.get<unknown>(lp('/v1/loop-findings/stats', profileId))
			),
		refetchInterval: 30000
	});
}

export async function fetchRunDetail(id: number): Promise<LoopRunDetail> {
	return LoopRunDetailSchema.parse(await api.get<unknown>(`/v1/loop-runs/${id}`));
}

// ---------------------------------------------------------------------------
// Single-turn review (from the Conversations view)
// ---------------------------------------------------------------------------

/**
 * Enqueue a review of ONE turn. The backend skips triage and forces
 * re-analysis, so an admin who asks for this turn always gets a fresh verdict.
 *
 * `profileId` must come from the conversation row, not the admin scope selector
 * — an admin working in global mode has no selected profile.
 */
export function reviewTurnMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async ({ turnId, profileId }: { turnId: string; profileId?: string | null }) =>
			LoopRunRequestSchema.parse(
				await api.post<unknown>('/v1/loop-config/run', {
					turn_id: turnId,
					profile_id: profileId ?? null,
					dry_run: false,
					force: true
				})
			),
		onSuccess: (_req, vars) => {
			qc.invalidateQueries({ queryKey: loopKeys.config() });
			// Drop any finding cached from an earlier review of this turn. Without
			// this, the second review would re-enable the query, hit a still-fresh
			// cache entry (staleTime 30s) and render the PREVIOUS verdict — the exact
			// stale-result failure the request-polling below exists to prevent.
			qc.invalidateQueries({ queryKey: loopKeys.findingByTurn(vars.turnId) });
		}
	});
}

/**
 * Poll one queued request until it settles.
 *
 * Polling the *finding* instead would be wrong: `loop_findings` is unique per
 * (profile, turn) and upserts, so a turn a previous run already analysed has a
 * row immediately — the UI would show last night's verdict and call it done.
 */
export function createRunRequestQuery(id: number | null) {
	// Reactive via `$derived(createRunRequestQuery(id))` at the call site — a
	// getter-closure form would never re-evaluate `enabled` and the poll would
	// never start (see the note on createTurnUsageQuery).
	return createQuery({
		queryKey: loopKeys.request(id ?? -1),
		enabled: id !== null,
		// Poll while queued/running, stop the moment it settles.
		refetchInterval: (query: { state: { data?: LoopRunRequest } }) =>
			isRequestSettled(query.state.data?.status) ? false : 3000,
		queryFn: async () =>
			LoopRunRequestSchema.parse(await api.get<unknown>(`/v1/loop-run-requests/${id}`))
	});
}

/** The finding for one turn — fetched only once its review request has settled. */
export function createFindingByTurnQuery(turnId: string | null | undefined, enabled: boolean) {
	return createQuery({
		queryKey: loopKeys.findingByTurn(turnId ?? ''),
		enabled: enabled && !!turnId,
		queryFn: async () => {
			const rows = LoopFindingListSchema.parse(
				await api.get<unknown>(`/v1/loop-findings?limit=1&turn_id=${encodeURIComponent(turnId!)}`)
			);
			return rows[0] ?? null;
		}
	});
}

// ---------------------------------------------------------------------------
// Finding → action
// ---------------------------------------------------------------------------

/** Add a finding's turn to the regression set. Upserts on the turn. */
export function createGoldenTestMutation() {
	const qc = useQueryClient();
	return createMutation({
		mutationFn: async (body: {
			source_turn_id: string;
			question: string;
			expected?: string | null;
			// What the eval asserts. Without these a hand-added case can never pass.
			expected_facts?: string[];
			forbidden_facts?: string[];
			citations?: string[];
			kind?: 'good_answer' | 'fixed_error';
			profile_id?: string | null;
		}) => GoldenTestSchema.parse(await api.post<unknown>('/v1/golden-tests', body)),
		onSuccess: () => qc.invalidateQueries({ queryKey: loopKeys.goldenTests() })
	});
}

export function createGoldenTestsQuery(profileId?: string | null, limit = 100) {
	return createQuery({
		queryKey: [...loopKeys.goldenTests(), profileId ?? '__global__', limit],
		queryFn: async () =>
			z
				.array(GoldenTestSchema)
				.parse(await api.get<unknown>(lp(`/v1/golden-tests?limit=${limit}`, profileId))),
		refetchInterval: 30000
	});
}

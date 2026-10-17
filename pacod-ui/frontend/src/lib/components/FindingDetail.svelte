<script lang="ts">
	/**
	 * One analysed turn, rendered identically wherever a finding is shown:
	 * the Review-process Findings view, the Review queue, and the per-turn review
	 * in Conversations.
	 *
	 * `actions` turns on the remediation controls. They exist because findings
	 * routed to `prompt_fix` / `retrieval` used to be read-only dead ends — the
	 * loop told you what was wrong and left you nowhere to go. Nothing here writes
	 * the curated tier; a created candidate still has to be approved.
	 */
	import {
		ExternalLink,
		Megaphone,
		FlaskConical,
		BookPlus,
		Check,
		AlertTriangle
	} from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import type { LoopFinding, TurnAnalysis } from '$lib/features/loop/queries';
	import {
		createGoldenTestMutation,
		updateFindingDispositionMutation
	} from '$lib/features/loop/queries';
	import { createCandidateMutation } from '$lib/features/kb-candidates/queries';
	import { createDirectiveMutation } from '$lib/features/directives/queries';
	import { m } from '$lib/paraglide/messages';

	let {
		finding,
		actions = false,
		profileId = null
	}: { finding: LoopFinding; actions?: boolean; profileId?: string | null } = $props();

	// The finding's own profile wins over anything the caller passes: the ambient
	// admin scope selector is blank in global mode, and every action below would
	// then silently land on the default agent instead of the one that produced
	// this finding. `profileId` is only a fallback for pre-`profile_id` rows.
	const targetProfile = $derived(finding.profile_id ?? profileId);

	function assessStyle(a: string | null): string {
		if (a === 'incorrect')
			return 'background: var(--color-danger-soft); color: var(--color-danger);';
		if (a === 'partial') return 'background: var(--color-surface-3); color: var(--color-text);';
		if (a === 'correct') return 'background: var(--color-brand-soft); color: var(--color-brand);';
		return 'background: var(--color-surface-3); color: var(--color-text-muted);';
	}

	// ---- Actions ----------------------------------------------------
	const goldenMut = createGoldenTestMutation();
	const candidateMut = createCandidateMutation();
	const directiveMut = createDirectiveMutation();
	const dispositionMut = updateFindingDispositionMutation();

	let actionError = $state('');
	let doneAction = $state<'golden' | 'candidate' | 'directive' | null>(null);

	type Disposition = 'useful' | 'dismissed' | 'duplicate' | 'fixed';

	function dispositionLabel(verdict: string): string {
		if (verdict === 'useful') return m.finding_disposition_useful();
		if (verdict === 'dismissed') return m.finding_disposition_dismissed();
		if (verdict === 'duplicate') return m.finding_disposition_duplicate();
		return m.finding_disposition_fixed();
	}

	async function setDisposition(verdict: Disposition | null) {
		actionError = '';
		try {
			await $dispositionMut.mutateAsync({
				id: finding.id,
				disposition: verdict,
				disposition_reason: verdict ? dispositionReason.trim() || null : null
			});
		} catch (e) {
			actionError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	// A prompt/behaviour failure is fixed by a directive, not by KB content.
	const wantsDirective = $derived(
		finding.route === 'prompt_fix' ||
			finding.failure_mode === 'agent_behavior' ||
			finding.failure_mode === 'tone' ||
			finding.failure_mode === 'hallucination'
	);
	// A retrieval failure means the content existed but wasn't surfaced — the
	// retrieval trace in the debug console is the thing worth looking at.
	const wantsTrace = $derived(
		finding.route === 'retrieval' || finding.failure_mode === 'retrieval'
	);
	// Only offer to create a candidate when the loop didn't already make one.
	const wantsCandidate = $derived(
		finding.candidate_id === null && finding.judge?.candidate_knowledge?.claim
	);

	// A wrong answer's golden test must store the CORRECTED answer and its
	// checkable facts — storing the answer that was wrong (as this used to) made
	// the regression set assert the bug. Without a fix there is nothing to assert.
	const isCorrect = $derived(finding.assessment === 'correct');
	const fix = $derived(finding.remediation);
	const canAddGolden = $derived(
		isCorrect || Boolean(fix?.corrected_answer && (fix?.checkable_facts?.length ?? 0) > 0)
	);

	async function addGolden() {
		actionError = '';
		try {
			await $goldenMut.mutateAsync({
				source_turn_id: finding.turn_id,
				question: finding.question ?? finding.turn_id,
				expected: isCorrect ? finding.answer : (fix?.corrected_answer ?? null),
				expected_facts: fix?.checkable_facts ?? [],
				forbidden_facts: isCorrect ? [] : (fix?.forbidden_facts ?? []),
				citations: fix?.citations ?? [],
				kind: isCorrect ? 'good_answer' : 'fixed_error',
				profile_id: targetProfile
			});
			doneAction = 'golden';
		} catch (e) {
			actionError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	let dispositionReason = $state('');

	async function addCandidate() {
		const ck = finding.judge?.candidate_knowledge;
		if (!ck?.claim) return;
		actionError = '';
		try {
			await $candidateMut.mutateAsync({
				topic: ck.topic || (finding.question ?? finding.turn_id).slice(0, 200),
				claim: ck.claim,
				review_notes: m.finding_candidate_note(),
				source_turn_id: finding.turn_id,
				profile_id: targetProfile
			});
			doneAction = 'candidate';
		} catch (e) {
			actionError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	async function addDirective() {
		actionError = '';
		// Prefer the remediation stage's imperative RULE. The fallback is the judge's
		// rationale, which is a description of what went wrong — pasting that into a
		// directive body produced an instruction nobody would ever activate. Kept only
		// for findings that predate the remediation stage.
		const proposal = finding.remediation?.directive_proposal;
		const rule = proposal?.rule?.trim();
		const content = rule || finding.judge?.rationale || finding.evidence || '';
		try {
			await $directiveMut.mutateAsync({
				title:
					proposal?.title?.trim() ||
					`${m.finding_directive_title_prefix()} ${(finding.question ?? finding.turn_id).slice(0, 80)}`,
				// Seeded as INACTIVE: a directive outranks tool data at synthesis time,
				// so it must be read and edited before it can steer live answers.
				content,
				language: 'all',
				legal_year: null,
				priority: 100,
				is_active: false,
				profile_id: targetProfile ?? undefined
			});
			doneAction = 'directive';
		} catch (e) {
			actionError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}
</script>

<!-- One analysis model's verdict (Analyse A / B). -->
{#snippet analysisBlock(label: string, a: TurnAnalysis | null)}
	<div>
		<div class="field-label">{label}</div>
		{#if a}
			<div class="mb-1 flex flex-wrap gap-2 text-xs">
				<span
					class="rounded-md px-1.5 py-0.5 font-medium"
					style={assessStyle(a.answer_assessment ?? null)}>{a.answer_assessment ?? '—'}</span
				>
				<span class="rounded-md bg-[var(--color-surface-3)] px-1.5 py-0.5"
					>{m.kb_mode()} {a.failure_mode ?? '—'}</span
				>
				<span class="rounded-md bg-[var(--color-surface-3)] px-1.5 py-0.5"
					>{m.kb_conf()} {a.confidence != null ? a.confidence.toFixed(2) : '—'}</span
				>
			</div>
			{#if a.user_information_need}<p class="text-xs text-[var(--color-text-muted)]">
					<strong>{m.kb_need()}</strong>
					{a.user_information_need}
				</p>{/if}
			{#if a.evidence}<p class="text-xs whitespace-pre-wrap">
					<strong>{m.kb_evidence()}</strong>
					{a.evidence}
				</p>{/if}
			{#if a.feedback_alignment}<p class="text-xs text-[var(--color-text-muted)]">
					<strong>{m.kb_feedback_alignment()}</strong>
					{a.feedback_alignment}
				</p>{/if}
			{#if a.candidate_knowledge?.topic || a.candidate_knowledge?.claim}
				<p class="text-xs">
					<strong>{m.kb_candidate()}</strong>
					{a.candidate_knowledge?.topic ?? ''}{a.candidate_knowledge?.claim
						? ` — ${a.candidate_knowledge.claim}`
						: ''}
				</p>
			{/if}
		{:else}
			<p class="text-xs text-[var(--color-text-muted)]">{m.kb_model_failed()}</p>
		{/if}
	</div>
{/snippet}

<div class="space-y-4 text-sm">
	<div class="flex flex-wrap gap-2 text-xs">
		<span class="rounded-md px-1.5 py-0.5 font-medium" style={assessStyle(finding.assessment)}
			>{finding.assessment ?? '—'}</span
		>
		<span class="rounded-md bg-[var(--color-surface-3)] px-1.5 py-0.5"
			>{m.kb_route_prefix()} {finding.route ?? '—'}</span
		>
		<span class="rounded-md bg-[var(--color-surface-3)] px-1.5 py-0.5"
			>{m.kb_novelty()} {finding.novelty ?? '—'}</span
		>
		<span class="rounded-md bg-[var(--color-surface-3)] px-1.5 py-0.5"
			>{m.kb_conf()}
			{finding.synthesis_confidence != null ? finding.synthesis_confidence.toFixed(2) : '—'}</span
		>
		<!-- A verdict reached without the sources is a guess; say so on the row itself
		     rather than leaving the reader to trust it equally. `partial` means the
		     sources were seen but cut — a fix is still possible. -->
		{#if finding.evidence_completeness === 'partial'}
			<span
				class="rounded-md px-1.5 py-0.5 font-medium"
				style="background: var(--color-surface-3); color: var(--color-text);"
				title={m.finding_evidence_partial_hint()}
			>
				{m.finding_evidence_partial()}
			</span>
		{:else if finding.evidence_completeness !== 'full'}
			<span
				class="rounded-md px-1.5 py-0.5 font-medium"
				style="background: var(--color-surface-3); color: var(--color-text-muted);"
				title={m.finding_evidence_degraded_hint()}
			>
				<AlertTriangle size={11} class="mb-px inline" />
				{m.finding_evidence_degraded()}
			</span>
		{/if}
		{#if finding.cluster_id != null}
			<a
				class="rounded-md bg-[var(--color-surface-3)] px-1.5 py-0.5 underline"
				href={`/review-process?cluster=${finding.cluster_id}`}
				>{m.finding_cluster_link({ id: finding.cluster_id })}</a
			>
		{/if}
		{#if finding.disposition}
			<span class="rounded-md bg-[var(--color-surface-3)] px-1.5 py-0.5">
				{m.finding_disposition_label()}
				{finding.disposition}
			</span>
		{/if}
	</div>
	<div>
		<div class="field-label">{m.kb_question()}</div>
		<p class="whitespace-pre-wrap">{finding.question ?? '—'}</p>
	</div>
	<div>
		<div class="field-label">{m.rp_answer_given()}</div>
		<p class="whitespace-pre-wrap text-[var(--color-text-muted)]">{finding.answer ?? '—'}</p>
	</div>
	<div>
		<div class="field-label">{m.rp_rationale_why()}</div>
		<p class="whitespace-pre-wrap">{finding.evidence ?? '—'}</p>
	</div>

	<!-- Per-stage pipeline detail -->
	{#if finding.triage}
		<div>
			<div class="field-label">{m.rp_triage_stage()}</div>
			<p class="text-xs">
				<span class="rounded-md bg-[var(--color-surface-3)] px-1.5 py-0.5"
					>{finding.triage.mode ?? '—'}</span
				>
				<span class="ml-1"
					>{m.rp_analyse_label()} {finding.triage.analyze ? m.rp_yes() : m.rp_no()}</span
				>
				<span class="ml-1 text-[var(--color-text-muted)]">· {finding.triage.reason ?? '—'}</span>
			</p>
		</div>
	{/if}

	{@render analysisBlock(m.kb_analysis_a(), finding.analysis_a)}
	{@render analysisBlock(m.kb_analysis_b(), finding.analysis_b)}

	{#if finding.judge}
		{@const j = finding.judge}
		<div>
			<div class="field-label">{m.kb_judge()}</div>
			<div class="mb-1 flex flex-wrap gap-2 text-xs">
				<span class="rounded-md bg-[var(--color-surface-3)] px-1.5 py-0.5"
					>{m.kb_agreement()} {j.agreement ?? '—'}</span
				>
				<span
					class="rounded-md px-1.5 py-0.5 font-medium"
					style={assessStyle(j.answer_assessment ?? null)}>{j.answer_assessment ?? '—'}</span
				>
				<span class="rounded-md bg-[var(--color-surface-3)] px-1.5 py-0.5"
					>{m.kb_mode()} {j.failure_mode ?? '—'}</span
				>
				<span class="rounded-md bg-[var(--color-surface-3)] px-1.5 py-0.5"
					>{m.kb_conf()}
					{j.synthesis_confidence != null ? j.synthesis_confidence.toFixed(2) : '—'}</span
				>
			</div>
			{#if j.conflict_note}<p class="text-xs whitespace-pre-wrap">
					<strong>{m.kb_conflict()}</strong>
					{j.conflict_note}
				</p>{/if}
			{#if j.rationale}<p class="text-xs whitespace-pre-wrap text-[var(--color-text-muted)]">
					<strong>{m.kb_rationale_prefix()}</strong>
					{j.rationale}
				</p>{/if}
		</div>
	{/if}

	<!-- The fix, not another description of the problem. This is the section that
	     turns a finding from a label into something a reviewer can act on. -->
	{#if finding.remediation?.corrected_answer || finding.remediation?.directive_proposal?.rule}
		{@const r = finding.remediation}
		<div
			class="rounded-lg border p-3"
			style="border-color: var(--color-border); background: var(--color-surface-2);"
		>
			<div class="field-label">{m.finding_remediation()}</div>
			{#if r.corrected_answer}
				<p class="mt-1 text-xs font-medium">{m.finding_corrected_answer()}</p>
				<p class="text-xs whitespace-pre-wrap">{r.corrected_answer}</p>
			{/if}
			{#if r.checkable_facts?.length}
				<p class="mt-2 text-xs">
					<strong>{m.finding_checkable_facts()}</strong>
					{#each r.checkable_facts as fact}
						<code class="mr-1 rounded bg-[var(--color-surface-3)] px-1">{fact}</code>
					{/each}
				</p>
			{/if}
			{#if r.forbidden_facts?.length}
				<p class="mt-1 text-xs">
					<strong>{m.finding_forbidden_facts()}</strong>
					{#each r.forbidden_facts as fact}
						<code
							class="mr-1 rounded px-1"
							style="background: var(--color-danger-soft); color: var(--color-danger);">{fact}</code
						>
					{/each}
				</p>
			{/if}
			{#if r.citations?.length}
				<p class="mt-1 text-xs text-[var(--color-text-muted)]">
					<strong>{m.finding_citations()}</strong>
					{r.citations.join(', ')}
				</p>
			{/if}
			{#if r.directive_proposal?.rule}
				<p class="mt-2 text-xs font-medium">{m.finding_proposed_rule()}</p>
				<p class="text-xs whitespace-pre-wrap italic">{r.directive_proposal.rule}</p>
				{#if r.directive_proposal.applies_when}
					<p class="text-xs text-[var(--color-text-muted)]">
						<strong>{m.finding_applies_when()}</strong>
						{r.directive_proposal.applies_when}
					</p>
				{/if}
			{/if}
			<p class="mt-2 text-xs text-[var(--color-text-muted)]">
				{#if r.remediation_kind}<span
						><strong>{m.finding_fix_kind()}</strong> {r.remediation_kind}</span
					>{/if}
				{#if r.rationale}<span class="ml-2"
						><strong>{m.finding_fix_rationale()}</strong> {r.rationale}</span
					>{/if}
			</p>
		</div>
	{:else if finding.assessment === 'incorrect' || finding.assessment === 'partial'}
		<!-- A wrong answer with no fix: say WHY, instead of a bare badge. -->
		<p class="text-xs text-[var(--color-text-muted)]">
			{finding.evidence_completeness === 'degraded'
				? m.finding_no_fix_degraded()
				: m.finding_no_fix()}
		</p>
	{/if}

	<p class="text-xs text-[var(--color-text-muted)]">
		{m.rp_turn_session({ turn: finding.turn_id, session: finding.session_id ?? '—' })}
	</p>

	{#if actions}
		<div class="border-t pt-4" style="border-color: var(--color-border);">
			<div class="field-label">{m.finding_actions()}</div>
			<div class="mt-1 flex flex-wrap gap-2">
				{#if wantsDirective}
					<Button
						variant="secondary"
						size="sm"
						onclick={addDirective}
						disabled={$directiveMut.isPending || doneAction === 'directive'}
					>
						{#if doneAction === 'directive'}<Check size={14} />{:else}<Megaphone size={14} />{/if}
						{doneAction === 'directive'
							? m.finding_directive_created()
							: m.finding_create_directive()}
					</Button>
				{/if}
				{#if wantsTrace}
					<a
						href={`/debug-console/${finding.turn_id}`}
						class="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-[var(--color-surface-2)]"
						style="border-color: var(--color-border); color: var(--color-text);"
					>
						<ExternalLink size={14} />
						{m.finding_open_trace()}
					</a>
				{/if}
				{#if wantsCandidate}
					<Button
						variant="secondary"
						size="sm"
						onclick={addCandidate}
						disabled={$candidateMut.isPending || doneAction === 'candidate'}
					>
						{#if doneAction === 'candidate'}<Check size={14} />{:else}<BookPlus size={14} />{/if}
						{doneAction === 'candidate'
							? m.finding_candidate_created()
							: m.finding_create_candidate()}
					</Button>
				{/if}
				<Button
					variant="secondary"
					size="sm"
					onclick={addGolden}
					disabled={$goldenMut.isPending || doneAction === 'golden' || !canAddGolden}
					title={canAddGolden ? m.finding_golden_hint_wrong() : m.finding_golden_needs_fix()}
				>
					{#if doneAction === 'golden'}<Check size={14} />{:else}<FlaskConical size={14} />{/if}
					{doneAction === 'golden' ? m.finding_golden_added() : m.finding_add_golden()}
				</Button>
			</div>
			{#if !canAddGolden}
				<p class="mt-1 text-xs text-[var(--color-text-muted)]">{m.finding_golden_needs_fix()}</p>
			{/if}
			{#if wantsDirective && doneAction === 'directive'}
				<p class="mt-2 text-xs text-[var(--color-text-muted)]">
					{m.finding_directive_inactive_note()}
				</p>
			{/if}

			<!-- Reviewer verdict on the FINDING. Deliberately NOT gated on
			     `needs_confirmation`: a finding without a KB candidate still needs to
			     be dismissable, and `dismissed` is what stops a false positive
			     resurfacing on every future run. -->
			<div class="mt-3 border-t pt-3" style="border-color: var(--color-border);">
				<div class="field-label">{m.finding_disposition()}</div>
				<input
					type="text"
					class="field-input mt-1 text-xs"
					placeholder={m.finding_disposition_reason()}
					bind:value={dispositionReason}
					maxlength="500"
				/>
				<div class="mt-1 flex flex-wrap gap-2">
					{#each ['useful', 'dismissed', 'duplicate', 'fixed'] as verdict}
						<Button
							variant={finding.disposition === verdict ? 'primary' : 'secondary'}
							size="sm"
							onclick={() => setDisposition(verdict as Disposition)}
							disabled={$dispositionMut.isPending}
						>
							{dispositionLabel(verdict)}
						</Button>
					{/each}
					{#if finding.disposition}
						<Button
							variant="secondary"
							size="sm"
							onclick={() => setDisposition(null)}
							disabled={$dispositionMut.isPending}
						>
							{m.finding_disposition_clear()}
						</Button>
					{/if}
				</div>
				<p class="mt-1 text-xs text-[var(--color-text-muted)]">{m.finding_disposition_hint()}</p>
			</div>

			{#if actionError}
				<p class="mt-2 text-xs" style="color: var(--color-danger);">{actionError}</p>
			{/if}
		</div>
	{/if}
</div>

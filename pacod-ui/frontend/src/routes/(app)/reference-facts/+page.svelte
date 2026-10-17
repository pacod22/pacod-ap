<script lang="ts">
	import { DEFAULT_PROFILE_ID } from '$lib/features/profiles/queries';
	import { Plus, Trash2, Pencil, X, Check, Search, Download, Upload } from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import {
		VALUE_TYPES,
		createReferenceFactListQuery,
		createReferenceFactCategoriesQuery,
		createReferenceFactMutation,
		updateReferenceFactMutation,
		deleteReferenceFactMutation,
		createImportReferenceFactsMutation,
		exportReferenceFacts,
		parseImportFile,
		formatFactValue,
		type ReferenceFact,
		type ReferenceFactFields,
		type ValueType
	} from '$lib/features/reference-facts/queries';
	import { selectedProfile } from '$lib/stores/selectedProfile.svelte';
	import { m } from '$lib/paraglide/messages';
	import { getLocale } from '$lib/paraglide/runtime';

	let filterCategory = $state('');
	let filterYear = $state('');
	let filterText = $state('');

	// The admin scope selector (topbar): null → all profiles; else one profile.
	let filters = $derived({
		profile_id: selectedProfile.id ?? undefined,
		category: filterCategory || undefined,
		legal_year: /^\d{4}$/.test(filterYear.trim()) ? Number(filterYear.trim()) : undefined,
		q: filterText.trim() || undefined,
		limit: 1000,
		offset: 0
	});

	const facts = $derived(createReferenceFactListQuery(filters));
	const categories = $derived(createReferenceFactCategoriesQuery(selectedProfile.id ?? null));
	const createMut = createReferenceFactMutation();
	const updateMut = updateReferenceFactMutation();
	const deleteMut = deleteReferenceFactMutation();
	const importMut = createImportReferenceFactsMutation();

	// Import/export always target one profile: the scoped one, else the default.
	let targetProfile = $derived(selectedProfile.id ?? DEFAULT_PROFILE_ID);

	const VALUE_TYPE_LABELS: Record<ValueType, () => string> = {
		number: m.reference_facts_type_number,
		percent: m.reference_facts_type_percent,
		count: m.reference_facts_type_count,
		money: m.reference_facts_type_money,
		range: m.reference_facts_type_range,
		date: m.reference_facts_type_date,
		annual_date: m.reference_facts_type_annual_date,
		monthly_day: m.reference_facts_type_monthly_day,
		text: m.reference_facts_type_text,
		boolean: m.reference_facts_type_boolean
	};

	// Which value inputs a type uses (the backend stores whatever is sent).
	const usesNum = (t: ValueType) =>
		['number', 'percent', 'count', 'money', 'range', 'monthly_day'].includes(t);
	const usesMax = (t: ValueType) => t === 'range';
	const usesUnit = (t: ValueType) => ['number', 'count', 'money', 'range'].includes(t);
	const usesText = (t: ValueType) => ['date', 'annual_date', 'text'].includes(t);

	function blankForm(): ReferenceFactFields {
		return {
			fact_key: '',
			category: null,
			variant: 'default',
			legal_year: new Date().getFullYear(),
			value_type: 'number',
			value_num: null,
			value_max: null,
			unit: null,
			value_text: null,
			extra: null,
			valid_from: null,
			valid_to: null,
			label_bg: null,
			label_en: null,
			description_bg: null,
			description_en: null,
			source_url: null,
			source_note: null
		};
	}

	let dialog = $state<HTMLDialogElement | null>(null);
	let editingId = $state<number | null>(null);
	let form = $state<ReferenceFactFields>(blankForm());
	// Text proxies for the inputs whose empty string means null.
	let yearText = $state('');
	let extraText = $state('');
	let formError = $state('');
	let confirmDeleteId = $state<number | null>(null);

	function openCreate() {
		editingId = null;
		form = blankForm();
		yearText = String(form.legal_year ?? '');
		extraText = '';
		formError = '';
		dialog?.showModal();
	}

	function openEdit(fact: ReferenceFact) {
		editingId = fact.id;
		const {
			id: _id,
			profile_id: _p,
			updated_by: _u,
			created_at: _c,
			updated_at: _a,
			...fields
		} = fact;
		form = { ...fields };
		yearText = fact.legal_year === null ? '' : String(fact.legal_year);
		extraText = fact.extra ? JSON.stringify(fact.extra, null, 2) : '';
		formError = '';
		dialog?.showModal();
	}

	function closeDialog() {
		dialog?.close();
		formError = '';
	}

	const blankToNull = (v: string | null | undefined) => (v && v.trim() ? v.trim() : null);

	function buildPayload(): ReferenceFactFields | null {
		const year = yearText.trim();
		if (year && !/^\d{4}$/.test(year)) {
			formError = m.reference_facts_err_year();
			return null;
		}
		let extra: Record<string, unknown> | null = null;
		if (extraText.trim()) {
			try {
				const parsed: unknown = JSON.parse(extraText);
				if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error();
				extra = parsed as Record<string, unknown>;
			} catch {
				formError = m.reference_facts_err_extra();
				return null;
			}
		}
		const t = form.value_type;
		const num = (n: number | null | undefined) =>
			typeof n === 'number' && !Number.isNaN(n) ? n : null;
		return {
			...form,
			fact_key: form.fact_key.trim(),
			category: blankToNull(form.category),
			variant: blankToNull(form.variant) ?? 'default',
			legal_year: year ? Number(year) : null,
			value_num: usesNum(t) || t === 'boolean' ? num(form.value_num) : null,
			value_max: usesMax(t) ? num(form.value_max) : null,
			unit: usesUnit(t) ? blankToNull(form.unit) : t === 'percent' ? '%' : null,
			value_text: usesText(t) ? blankToNull(form.value_text) : null,
			extra,
			valid_from: blankToNull(form.valid_from),
			valid_to: blankToNull(form.valid_to),
			label_bg: blankToNull(form.label_bg),
			label_en: blankToNull(form.label_en),
			description_bg: blankToNull(form.description_bg),
			description_en: blankToNull(form.description_en),
			source_url: blankToNull(form.source_url),
			source_note: blankToNull(form.source_note)
		};
	}

	async function submitForm() {
		formError = '';
		const payload = buildPayload();
		if (!payload) return;
		try {
			if (editingId !== null) {
				await $updateMut.mutateAsync({ id: editingId, patch: payload });
			} else {
				// Create into the currently-scoped profile (else the default profile).
				await $createMut.mutateAsync({
					...payload,
					profile_id: selectedProfile.id ?? DEFAULT_PROFILE_ID
				});
			}
			closeDialog();
		} catch (e: unknown) {
			formError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	async function confirmDelete(id: number) {
		try {
			await $deleteMut.mutateAsync(id);
			confirmDeleteId = null;
		} catch {
			// invalidation is in the mutation
		}
	}

	function clearFilters() {
		filterCategory = '';
		filterYear = '';
		filterText = '';
	}
	const hasFilters = $derived(!!(filterCategory || filterYear || filterText));

	// ---- Export / import ---------------------------------------------------
	let exportError = $state('');
	async function downloadExport() {
		exportError = '';
		try {
			const rows = await exportReferenceFacts(targetProfile);
			const blob = new Blob([JSON.stringify(rows, null, 2)], { type: 'application/json' });
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = `reference-facts-${targetProfile}.json`;
			a.click();
			URL.revokeObjectURL(url);
		} catch (e: unknown) {
			exportError = e instanceof Error ? e.message : m.common_unexpected_error();
		}
	}

	let importDialog = $state<HTMLDialogElement | null>(null);
	let importFacts = $state<ReferenceFactFields[] | null>(null);
	let importReplace = $state(false);
	let importError = $state('');
	let importResult = $state<string | null>(null);

	function openImport() {
		importFacts = null;
		importReplace = false;
		importError = '';
		importResult = null;
		importDialog?.showModal();
	}

	async function onImportFile(e: Event) {
		importError = '';
		importResult = null;
		const file = (e.target as HTMLInputElement).files?.[0];
		if (!file) return;
		try {
			importFacts = parseImportFile(await file.text());
		} catch (err: unknown) {
			importFacts = null;
			importError = err instanceof Error ? err.message : m.common_unexpected_error();
		}
	}

	async function runImport() {
		if (!importFacts) return;
		importError = '';
		try {
			const r = await $importMut.mutateAsync({
				profileId: targetProfile,
				facts: importFacts,
				replace: importReplace
			});
			importResult = m.reference_facts_import_done({
				created: r.created,
				updated: r.updated,
				deleted: r.deleted
			});
		} catch (err: unknown) {
			importError = err instanceof Error ? err.message : m.common_unexpected_error();
		}
	}

	function factLabel(f: ReferenceFact): string {
		const bg = getLocale() === 'bg';
		return (bg ? f.label_bg || f.label_en : f.label_en || f.label_bg) ?? '';
	}
</script>

<svelte:head><title>{m.reference_facts_title()}</title></svelte:head>

<div class="space-y-6">
	<!-- Header -->
	<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
		<div>
			<h1 class="text-2xl font-semibold tracking-tight">{m.reference_facts_heading()}</h1>
			<p class="mt-1 text-sm text-[var(--color-text-muted)]">
				{m.reference_facts_intro()}
			</p>
		</div>
		<div class="flex flex-wrap gap-2">
			<Button size="sm" variant="secondary" onclick={downloadExport}>
				<Download size={14} />
				{m.reference_facts_export()}
			</Button>
			<Button size="sm" variant="secondary" onclick={openImport}>
				<Upload size={14} />
				{m.reference_facts_import()}
			</Button>
			<Button size="sm" onclick={openCreate}>
				<Plus size={14} />
				{m.reference_facts_new()}
			</Button>
		</div>
	</div>
	{#if exportError}
		<p class="text-sm" style="color: var(--color-danger);">{exportError}</p>
	{/if}

	<!-- Filters -->
	<div
		class="flex flex-col flex-wrap gap-2 rounded-xl border p-3 sm:flex-row sm:items-center"
		style="background: var(--color-surface-2); border-color: var(--color-border);"
	>
		<select
			bind:value={filterCategory}
			class="field-input h-10 w-full sm:w-48"
			aria-label={m.reference_facts_filter_category()}
		>
			<option value="">{m.reference_facts_filter_category()}</option>
			{#each $categories.data ?? [] as cat (cat)}
				<option value={cat}>{cat}</option>
			{/each}
		</select>
		<label
			class="flex h-10 w-full items-center gap-2 rounded-lg border px-3 transition-colors focus-within:border-[var(--color-brand)] focus-within:shadow-[0_0_0_3px_color-mix(in_oklch,var(--color-brand)_18%,transparent)] sm:w-36"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<Search size={13} class="shrink-0 text-[var(--color-text-muted)]" />
			<input
				type="text"
				inputmode="numeric"
				placeholder={m.common_reference_year()}
				bind:value={filterYear}
				class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--color-text-muted)] placeholder:opacity-70"
			/>
		</label>
		<label
			class="flex h-10 w-full items-center gap-2 rounded-lg border px-3 transition-colors focus-within:border-[var(--color-brand)] focus-within:shadow-[0_0_0_3px_color-mix(in_oklch,var(--color-brand)_18%,transparent)] sm:w-64"
			style="background: var(--color-surface-2); border-color: var(--color-border);"
		>
			<Search size={13} class="shrink-0 text-[var(--color-text-muted)]" />
			<input
				type="text"
				placeholder={m.reference_facts_filter_text()}
				bind:value={filterText}
				class="noborder min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--color-text-muted)] placeholder:opacity-70"
			/>
		</label>
		{#if hasFilters}
			<Button variant="ghost" size="sm" onclick={clearFilters}>
				<X size={14} />
				{m.common_clear()}
			</Button>
		{/if}
	</div>

	<!-- Table -->
	<div class="overflow-hidden rounded-xl border" style="border-color: var(--color-border);">
		<div class="overflow-x-auto">
			<table class="data-table data-table-stack">
				<thead>
					<tr>
						<th>{m.reference_facts_col_key()}</th>
						<th>{m.reference_facts_col_variant()}</th>
						<th>{m.reference_facts_col_category()}</th>
						<th>{m.common_reference_year()}</th>
						<th>{m.reference_facts_col_value()}</th>
						<th>{m.reference_facts_col_label()}</th>
						<th>{m.reference_facts_col_source()}</th>
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#if $facts.isPending}
						<tr>
							<td colspan="8" class="py-12 text-center text-sm text-[var(--color-text-muted)]">
								{m.reference_facts_loading()}
							</td>
						</tr>
					{:else if $facts.isError}
						<tr>
							<td colspan="8" class="py-12 text-center text-sm" style="color: var(--color-danger);">
								{m.reference_facts_load_failed()}
							</td>
						</tr>
					{:else if ($facts.data ?? []).length > 0}
						{#each $facts.data ?? [] as fact (fact.id)}
							<tr>
								<td data-label={m.reference_facts_col_key()}>
									<span class="font-mono text-xs font-medium">{fact.fact_key}</span>
								</td>
								<td
									data-label={m.reference_facts_col_variant()}
									class="text-[var(--color-text-muted)]">{fact.variant}</td
								>
								<td
									data-label={m.reference_facts_col_category()}
									class="text-[var(--color-text-muted)]"
								>
									{#if fact.category}
										<span class="font-mono text-xs">{fact.category}</span>
									{:else}
										<span>—</span>
									{/if}
								</td>
								<td data-label={m.common_reference_year()} class="tabular-nums">
									{fact.legal_year ?? m.reference_facts_every_year()}
								</td>
								<td data-label={m.reference_facts_col_value()} class="tabular-nums">
									<span
										class="rounded-md px-1.5 py-0.5 text-xs font-medium"
										style="background: var(--color-brand-soft); color: var(--color-brand);"
										title={VALUE_TYPE_LABELS[fact.value_type]()}
									>
										{formatFactValue(fact)}
									</span>
								</td>
								<td
									data-label={m.reference_facts_col_label()}
									class="stack-block max-w-[260px] truncate text-[var(--color-text-muted)]"
									title={factLabel(fact)}
								>
									{factLabel(fact) || '—'}
								</td>
								<td data-label={m.reference_facts_col_source()} class="max-w-[160px] truncate">
									{#if fact.source_url}
										<a
											href={fact.source_url}
											target="_blank"
											rel="noopener noreferrer"
											class="text-xs underline"
											style="color: var(--color-brand);">{fact.source_url}</a
										>
									{:else}
										<span class="text-[var(--color-text-muted)]">—</span>
									{/if}
								</td>
								<td data-label="" class="w-20">
									{#if confirmDeleteId === fact.id}
										<div class="flex items-center justify-end gap-1">
											<button
												onclick={() => confirmDelete(fact.id)}
												class="touch-target rounded-lg p-1.5 transition-colors"
												style="color: var(--color-danger);"
												aria-label={m.common_confirm_delete()}
												title={m.common_confirm_delete()}
											>
												<Check size={14} />
											</button>
											<button
												onclick={() => (confirmDeleteId = null)}
												class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-3)]"
												aria-label={m.common_cancel()}
												title={m.common_cancel()}
											>
												<X size={14} />
											</button>
										</div>
									{:else}
										<div class="flex items-center justify-end gap-1">
											<button
												onclick={() => openEdit(fact)}
												class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-3)] hover:text-[var(--color-text)]"
												aria-label={m.common_edit()}
												title={m.common_edit()}
											>
												<Pencil size={14} />
											</button>
											<button
												onclick={() => (confirmDeleteId = fact.id)}
												class="touch-target rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-danger)]"
												aria-label={m.common_delete()}
												title={m.common_delete()}
											>
												<Trash2 size={14} />
											</button>
										</div>
									{/if}
								</td>
							</tr>
						{/each}
					{:else}
						<tr>
							<td colspan="8" class="py-12 text-center text-sm text-[var(--color-text-muted)]">
								{hasFilters ? m.reference_facts_none_match() : m.reference_facts_empty()}
							</td>
						</tr>
					{/if}
				</tbody>
			</table>
		</div>
	</div>

	{#if !$facts.isPending && ($facts.data?.length ?? 0) > 0}
		<p class="text-sm text-[var(--color-text-muted)]">
			{m.common_results({ n: $facts.data?.length ?? 0 })}
			{#if hasFilters}
				{m.common_matching_filters()}{/if}
		</p>
	{/if}
</div>

<!-- ------------------------------------------------------------------ -->
<!-- Create / Edit dialog -->
<!-- ------------------------------------------------------------------ -->
<dialog
	bind:this={dialog}
	class="mx-auto max-h-[90dvh] w-[calc(100%-2rem)] max-w-xl overflow-y-auto rounded-2xl p-0 shadow-2xl"
	style="background: var(--color-surface); border: 1px solid var(--color-border);"
>
	<form
		onsubmit={(e) => {
			e.preventDefault();
			submitForm();
		}}
	>
		<div
			class="flex items-center justify-between border-b px-6 py-4"
			style="border-color: var(--color-border);"
		>
			<h2 class="text-base font-semibold">
				{editingId ? m.reference_facts_edit_title() : m.reference_facts_new_title()}
			</h2>
			<button
				type="button"
				onclick={closeDialog}
				class="rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
				aria-label={m.common_close()}
			>
				<X size={16} />
			</button>
		</div>

		<div class="space-y-4 px-6 py-5">
			<!-- Identity -->
			<div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
				<div class="sm:col-span-2">
					<label class="field-label" for="rf-key">{m.reference_facts_field_key()}</label>
					<input
						id="rf-key"
						type="text"
						bind:value={form.fact_key}
						required
						maxlength="128"
						pattern="[A-Za-z0-9_.:\-]+"
						placeholder={m.reference_facts_field_key_ph()}
						class="field-input font-mono"
					/>
				</div>
				<div>
					<label class="field-label" for="rf-variant">{m.reference_facts_field_variant()}</label>
					<input
						id="rf-variant"
						type="text"
						bind:value={form.variant}
						maxlength="64"
						placeholder="default"
						class="field-input"
					/>
				</div>
			</div>
			<div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
				<div>
					<label class="field-label" for="rf-category">{m.reference_facts_field_category()}</label>
					<input
						id="rf-category"
						type="text"
						bind:value={form.category}
						maxlength="64"
						list="rf-categories"
						class="field-input"
					/>
					<datalist id="rf-categories">
						{#each $categories.data ?? [] as cat (cat)}
							<option value={cat}></option>
						{/each}
					</datalist>
				</div>
				<div>
					<label class="field-label" for="rf-year">{m.common_reference_year()}</label>
					<input
						id="rf-year"
						type="text"
						inputmode="numeric"
						bind:value={yearText}
						placeholder={m.reference_facts_every_year()}
						class="field-input"
					/>
				</div>
				<div>
					<label class="field-label" for="rf-type">{m.reference_facts_field_type()}</label>
					<select id="rf-type" bind:value={form.value_type} class="field-input">
						{#each VALUE_TYPES as t (t)}
							<option value={t}>{VALUE_TYPE_LABELS[t]()}</option>
						{/each}
					</select>
				</div>
			</div>

			<!-- Value (inputs follow the type) -->
			<div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
				{#if usesNum(form.value_type)}
					<div>
						<label class="field-label" for="rf-num">
							{form.value_type === 'range'
								? m.reference_facts_field_min()
								: form.value_type === 'monthly_day'
									? m.reference_facts_field_day()
									: m.reference_facts_field_value()}
						</label>
						<input
							id="rf-num"
							type="number"
							step="any"
							min={form.value_type === 'monthly_day' ? 1 : undefined}
							max={form.value_type === 'monthly_day' ? 31 : undefined}
							bind:value={form.value_num}
							class="field-input"
						/>
					</div>
				{/if}
				{#if usesMax(form.value_type)}
					<div>
						<label class="field-label" for="rf-max">{m.reference_facts_field_max()}</label>
						<input
							id="rf-max"
							type="number"
							step="any"
							bind:value={form.value_max}
							class="field-input"
						/>
					</div>
				{/if}
				{#if usesUnit(form.value_type)}
					<div>
						<label class="field-label" for="rf-unit">{m.reference_facts_field_unit()}</label>
						<input
							id="rf-unit"
							type="text"
							bind:value={form.unit}
							maxlength="32"
							placeholder={m.reference_facts_field_unit_ph()}
							class="field-input"
						/>
					</div>
				{/if}
				{#if form.value_type === 'boolean'}
					<div>
						<label class="field-label" for="rf-bool">{m.reference_facts_field_value()}</label>
						<select id="rf-bool" bind:value={form.value_num} class="field-input">
							<option value={1}>{m.reference_facts_bool_yes()}</option>
							<option value={0}>{m.reference_facts_bool_no()}</option>
						</select>
					</div>
				{/if}
				{#if form.value_type === 'date'}
					<div>
						<label class="field-label" for="rf-date">{m.reference_facts_field_value()}</label>
						<input id="rf-date" type="date" bind:value={form.value_text} class="field-input" />
					</div>
				{/if}
				{#if form.value_type === 'annual_date'}
					<div>
						<label class="field-label" for="rf-annual">{m.reference_facts_field_annual()}</label>
						<input
							id="rf-annual"
							type="text"
							bind:value={form.value_text}
							pattern="(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])"
							placeholder="MM-DD"
							class="field-input"
						/>
					</div>
				{/if}
			</div>
			{#if form.value_type === 'text'}
				<div>
					<label class="field-label" for="rf-text">{m.reference_facts_field_value()}</label>
					<textarea id="rf-text" bind:value={form.value_text} rows={2} class="field-textarea"
					></textarea>
				</div>
			{/if}

			<!-- Labels and descriptions -->
			<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
				<div>
					<label class="field-label" for="rf-label-en">{m.reference_facts_field_label_en()}</label>
					<input
						id="rf-label-en"
						type="text"
						bind:value={form.label_en}
						maxlength="256"
						class="field-input"
					/>
				</div>
				<div>
					<label class="field-label" for="rf-label-bg">{m.reference_facts_field_label_bg()}</label>
					<input
						id="rf-label-bg"
						type="text"
						bind:value={form.label_bg}
						maxlength="256"
						class="field-input"
					/>
				</div>
			</div>
			<div>
				<label class="field-label" for="rf-desc-en">{m.reference_facts_field_desc_en()}</label>
				<textarea id="rf-desc-en" bind:value={form.description_en} rows={2} class="field-textarea"
				></textarea>
			</div>
			<div>
				<label class="field-label" for="rf-desc-bg">{m.reference_facts_field_desc_bg()}</label>
				<textarea id="rf-desc-bg" bind:value={form.description_bg} rows={2} class="field-textarea"
				></textarea>
			</div>

			<!-- Validity -->
			<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
				<div>
					<label class="field-label" for="rf-from">{m.reference_facts_field_valid_from()}</label>
					<input id="rf-from" type="date" bind:value={form.valid_from} class="field-input" />
				</div>
				<div>
					<label class="field-label" for="rf-to">{m.reference_facts_field_valid_to()}</label>
					<input id="rf-to" type="date" bind:value={form.valid_to} class="field-input" />
				</div>
			</div>

			<!-- Source -->
			<div>
				<label class="field-label" for="rf-source">{m.reference_facts_field_source_url()}</label>
				<input
					id="rf-source"
					type="url"
					bind:value={form.source_url}
					maxlength="512"
					placeholder="https://…"
					class="field-input"
				/>
			</div>
			<div>
				<label class="field-label" for="rf-note">{m.reference_facts_field_source_note()}</label>
				<input id="rf-note" type="text" bind:value={form.source_note} class="field-input" />
			</div>

			<!-- Extra attributes -->
			<div>
				<label class="field-label" for="rf-extra">{m.reference_facts_field_extra()}</label>
				<textarea
					id="rf-extra"
					bind:value={extraText}
					rows={3}
					placeholder={'{ "amount_other_currency": 100 }'}
					class="field-textarea font-mono text-xs"></textarea>
				<p class="mt-1 text-xs text-[var(--color-text-muted)]">
					{m.reference_facts_field_extra_hint()}
				</p>
			</div>
		</div>

		{#if formError}
			<div
				class="mx-6 mb-2 rounded-lg px-3 py-2 text-sm"
				style="background: var(--color-danger-soft); color: var(--color-danger);"
			>
				{formError}
			</div>
		{/if}

		<div
			class="flex justify-end gap-2 border-t px-6 py-4"
			style="border-color: var(--color-border);"
		>
			<Button type="button" variant="secondary" size="sm" onclick={closeDialog}
				>{m.common_cancel()}</Button
			>
			<Button type="submit" size="sm" disabled={$createMut.isPending || $updateMut.isPending}>
				{editingId ? m.common_save_changes() : m.common_create()}
			</Button>
		</div>
	</form>
</dialog>

<!-- ------------------------------------------------------------------ -->
<!-- Import dialog -->
<!-- ------------------------------------------------------------------ -->
<dialog
	bind:this={importDialog}
	class="mx-auto w-[calc(100%-2rem)] max-w-lg rounded-2xl p-0 shadow-2xl"
	style="background: var(--color-surface); border: 1px solid var(--color-border);"
>
	<div
		class="flex items-center justify-between border-b px-6 py-4"
		style="border-color: var(--color-border);"
	>
		<h2 class="text-base font-semibold">{m.reference_facts_import_title()}</h2>
		<button
			type="button"
			onclick={() => importDialog?.close()}
			class="rounded-lg p-1.5 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
			aria-label={m.common_close()}
		>
			<X size={16} />
		</button>
	</div>
	<div class="space-y-4 px-6 py-5">
		<p class="text-sm text-[var(--color-text-muted)]">
			{m.reference_facts_import_intro({ profile: targetProfile })}
		</p>
		<input type="file" accept="application/json,.json" onchange={onImportFile} class="text-sm" />
		{#if importFacts}
			<p class="text-sm">{m.reference_facts_import_ready({ n: importFacts.length })}</p>
		{/if}
		<label class="flex items-center gap-2 text-sm">
			<input type="checkbox" bind:checked={importReplace} />
			{m.reference_facts_import_replace()}
		</label>
		{#if importError}
			<p class="text-sm" style="color: var(--color-danger);">{importError}</p>
		{/if}
		{#if importResult}
			<p class="text-sm" style="color: var(--color-success);">{importResult}</p>
		{/if}
	</div>
	<div class="flex justify-end gap-2 border-t px-6 py-4" style="border-color: var(--color-border);">
		<Button type="button" variant="secondary" size="sm" onclick={() => importDialog?.close()}
			>{m.common_close()}</Button
		>
		<Button
			type="button"
			size="sm"
			disabled={!importFacts || $importMut.isPending}
			onclick={runImport}>{m.reference_facts_import_run()}</Button
		>
	</div>
</dialog>

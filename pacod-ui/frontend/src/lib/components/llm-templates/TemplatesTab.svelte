<script lang="ts">
	/**
	 * "Templates" tab of LLM & Search: the global list of LLM templates, which
	 * roles use each, and add / edit / duplicate / delete. Ignores the top-bar
	 * profile selector — templates are platform-wide.
	 */
	import { Copy, Layers, Pencil, Plus, Trash2 } from 'lucide-svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import { m } from '$lib/paraglide/messages';
	import TemplateDialog from './TemplateDialog.svelte';
	import { usageLabel } from './labels';
	import {
		createTemplatesQuery,
		deleteTemplateMutation,
		inUseUsages,
		templateErrorMessage,
		templateSummary,
		type LlmTemplate,
		type TemplateUsage
	} from '$lib/features/llm-config/templates';

	const q = createTemplatesQuery();
	const deleteMut = deleteTemplateMutation();

	let editor = $state<TemplateDialog | null>(null);
	let confirmDelete = $state<string | null>(null);
	let deleteError = $state<{ id: string; message: string; usages: TemplateUsage[] } | null>(null);
	let expanded = $state<string | null>(null);

	async function remove(t: LlmTemplate) {
		deleteError = null;
		try {
			await $deleteMut.mutateAsync(t.id);
			confirmDelete = null;
		} catch (e) {
			confirmDelete = null;
			deleteError = {
				id: t.id,
				message: templateErrorMessage(e, m.common_unexpected_error()),
				usages: inUseUsages(e)
			};
		}
	}
</script>

<section class="space-y-4">
	<div class="flex flex-wrap items-start justify-between gap-3">
		<div>
			<div class="flex items-center gap-2">
				<Layers size={16} />
				<h2 class="text-base font-semibold">{m.llmtpl_heading()}</h2>
			</div>
			<p class="mt-1 max-w-3xl text-sm text-[var(--color-text-muted)]">{m.llmtpl_intro()}</p>
		</div>
		<Button size="sm" onclick={() => editor?.open()} disabled={!$q.data}>
			<Plus size={14} />
			{m.llmtpl_add()}
		</Button>
	</div>

	{#if $q.isPending}
		<p class="text-sm text-[var(--color-text-muted)]">{m.common_loading()}</p>
	{:else if $q.isError}
		<p class="text-sm" style="color: var(--color-danger);">{m.llmtpl_load_failed()}</p>
	{:else if $q.data}
		<div class="overflow-x-auto rounded-lg" style="border: 1px solid var(--color-border);">
			<table class="w-full text-sm">
				<thead>
					<tr
						class="text-left text-[var(--color-text-muted)]"
						style="background: var(--color-surface-2);"
					>
						<th class="px-3 py-2 font-medium">{m.llmtpl_col_name()}</th>
						<th class="px-3 py-2 font-medium">{m.llmtpl_col_params()}</th>
						<th class="px-3 py-2 font-medium">{m.llmtpl_col_used_by()}</th>
						<th class="px-3 py-2 text-right font-medium">{m.llmtpl_col_actions()}</th>
					</tr>
				</thead>
				<tbody>
					{#if $q.data.templates.length === 0}
						<tr style="border-top: 1px solid var(--color-border);">
							<td colspan="4" class="px-3 py-6 text-center text-[var(--color-text-muted)]"
								>{m.llmtpl_empty()}</td
							>
						</tr>
					{/if}
					{#each $q.data.templates as t (t.id)}
						<tr class="align-top" style="border-top: 1px solid var(--color-border);">
							<td class="px-3 py-2">
								<div class="font-medium text-[var(--color-text)]">{t.name}</div>
								<div class="font-mono text-[11px] text-[var(--color-text-muted)]">{t.id}</div>
								{#if t.description}
									<div class="mt-0.5 text-xs text-[var(--color-text-muted)]">{t.description}</div>
								{/if}
							</td>
							<td class="px-3 py-2">
								<div class="font-mono text-xs">{templateSummary(t)}</div>
								{#if t.top_p !== null || t.frequency_penalty !== null || t.presence_penalty !== null}
									<div class="font-mono text-[11px] text-[var(--color-text-muted)]">
										{[
											t.top_p !== null ? `top_p=${t.top_p}` : '',
											t.frequency_penalty !== null ? `freq=${t.frequency_penalty}` : '',
											t.presence_penalty !== null ? `pres=${t.presence_penalty}` : ''
										]
											.filter(Boolean)
											.join(' · ')}
									</div>
								{/if}
								{#if t.ignored_params.length > 0}
									<span
										class="mt-1 inline-block rounded-md px-2 py-0.5 text-[11px]"
										style="background: var(--color-warning-soft); color: var(--color-warning);"
										>{m.llmtpl_ignored({ params: t.ignored_params.join(', ') })}</span
									>
								{/if}
							</td>
							<td class="px-3 py-2 text-xs">
								{#if t.usages.length === 0}
									<span class="text-[var(--color-text-muted)]">{m.llmtpl_unused()}</span>
								{:else}
									<button
										type="button"
										class="underline decoration-dotted"
										onclick={() => (expanded = expanded === t.id ? null : t.id)}
										>{m.llmtpl_used_by_count({ count: t.usages.length })}</button
									>
									{#if expanded === t.id}
										<ul class="mt-1 space-y-0.5 text-[var(--color-text-muted)]">
											{#each t.usages as u (`${u.profile_id}:${u.group}:${u.role}`)}
												<li>{usageLabel(u)}</li>
											{/each}
										</ul>
									{/if}
								{/if}
								{#if deleteError?.id === t.id}
									<div class="mt-2" style="color: var(--color-danger);">
										<p>{deleteError.usages.length ? m.llmtpl_in_use() : deleteError.message}</p>
										{#if deleteError.usages.length}
											<ul class="mt-1 list-inside list-disc">
												{#each deleteError.usages as u (`${u.profile_id}:${u.group}:${u.role}`)}
													<li>{usageLabel(u)}</li>
												{/each}
											</ul>
										{/if}
									</div>
								{/if}
							</td>
							<td class="px-3 py-2">
								<div class="flex justify-end gap-1">
									{#if confirmDelete === t.id}
										<Button size="xs" variant="danger" onclick={() => remove(t)}
											>{m.common_confirm()}</Button
										>
										<Button size="xs" variant="ghost" onclick={() => (confirmDelete = null)}
											>{m.common_cancel()}</Button
										>
									{:else}
										<Button
											size="icon"
											variant="ghost"
											onclick={() => editor?.open(t)}
											aria-label={m.common_edit()}
											title={m.common_edit()}><Pencil size={14} /></Button
										>
										<Button
											size="icon"
											variant="ghost"
											onclick={() => editor?.open(t, true)}
											aria-label={m.llmtpl_duplicate()}
											title={m.llmtpl_duplicate()}><Copy size={14} /></Button
										>
										<Button
											size="icon"
											variant="danger-ghost"
											onclick={() => {
												deleteError = null;
												confirmDelete = t.id;
											}}
											aria-label={m.common_delete()}
											title={m.llmtpl_confirm_delete()}><Trash2 size={14} /></Button
										>
									{/if}
								</div>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>

		<TemplateDialog
			bind:this={editor}
			models={$q.data.available_models}
			capabilities={$q.data.capabilities}
		/>
	{/if}
</section>

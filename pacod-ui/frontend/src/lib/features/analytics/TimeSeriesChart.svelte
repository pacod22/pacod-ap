<script lang="ts">
	/**
	 * Shared Chart.js time-series chart for the analytics pages.
	 *
	 * Unlike the inline chart in UsageTab, the Chart instance is tied to the
	 * canvas element it was created on: if the canvas is replaced (e.g. an
	 * `{#if}` around it toggles) the old chart is destroyed and a new one built,
	 * so it never keeps drawing into a detached canvas.
	 */
	import { browser } from '$app/environment';
	import {
		Chart,
		BarController,
		BarElement,
		LineController,
		LineElement,
		PointElement,
		CategoryScale,
		LinearScale,
		Filler,
		Tooltip,
		Legend
	} from 'chart.js';
	import type { SeriesDef } from './format';

	Chart.register(
		BarController,
		BarElement,
		LineController,
		LineElement,
		PointElement,
		CategoryScale,
		LinearScale,
		Filler,
		Tooltip,
		Legend
	);

	let {
		labels,
		series,
		height = 240,
		stacked = false,
		format = (v: number) => v.toLocaleString()
	}: {
		labels: string[];
		series: SeriesDef[];
		height?: number;
		stacked?: boolean;
		format?: (v: number) => string;
	} = $props();

	let canvas = $state<HTMLCanvasElement | null>(null);
	// Plain (non-reactive) handles: reading/writing them must not re-trigger the effect.
	let chart: Chart | null = null;
	let chartCanvas: HTMLCanvasElement | null = null;

	function destroy() {
		chart?.destroy();
		chart = null;
		chartCanvas = null;
	}

	$effect(() => {
		if (!browser || !canvas) {
			destroy();
			return;
		}
		const fmt = format;
		const datasets = series.map((s) => ({
			type: (s.bar ? 'bar' : 'line') as 'bar' | 'line',
			label: s.label,
			data: s.data,
			borderColor: s.color,
			backgroundColor: s.bar ? s.color : s.color + '22',
			borderWidth: s.bar ? 0 : 2,
			pointRadius: 0,
			pointHoverRadius: 3,
			tension: 0.25,
			fill: !s.bar && !stacked
		}));
		const labelsCopy = [...labels];

		if (chart && chartCanvas !== canvas) destroy();

		if (!chart) {
			chartCanvas = canvas;
			chart = new Chart(canvas, {
				type: 'line',
				data: { labels: labelsCopy, datasets },
				options: {
					responsive: true,
					maintainAspectRatio: false,
					interaction: { mode: 'index', intersect: false },
					plugins: {
						legend: {
							display: true,
							position: 'bottom',
							labels: { boxWidth: 12, font: { size: 10 } }
						},
						tooltip: {
							callbacks: { label: (c) => `${c.dataset.label}: ${fmt(Number(c.raw))}` }
						}
					},
					scales: {
						x: {
							stacked,
							grid: { display: false },
							ticks: { autoSkip: true, maxRotation: 0 }
						},
						y: {
							stacked,
							beginAtZero: true,
							ticks: { callback: (v) => fmt(Number(v)) }
						}
					}
				}
			});
		} else {
			chart.data.labels = labelsCopy;
			chart.data.datasets = datasets;
			const tt = chart.options.plugins?.tooltip;
			if (tt?.callbacks) tt.callbacks.label = (c) => `${c.dataset.label}: ${fmt(Number(c.raw))}`;
			const y = chart.options.scales?.y;
			if (y && 'ticks' in y && y.ticks) y.ticks.callback = (v) => fmt(Number(v));
			chart.update();
		}
	});

	$effect(() => () => destroy());
</script>

<div style="height: {height}px;"><canvas bind:this={canvas}></canvas></div>

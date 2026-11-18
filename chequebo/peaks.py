"""Observed process context, never a causal inference from coincident peaks."""

import math


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def peak_context(sample, key):
    """Plain text shared by chart tooltips, the inspector and exported reports."""
    metric, label, unit = {
        'memory_mib': ('rss_mib', 'Largest resident-memory processes', 'GiB'),
        'ram_mib': ('rss_mib', 'Largest resident-memory processes', 'GiB'),
        'cpu_percent': ('cpu_percent', 'Highest observed CPU usage', '% of total CPU'),
        'vram_mib': ('vram_mib', 'Largest reported NVIDIA VRAM allocations', 'GiB'),
    }.get(key, (None, None, None))
    unavailable = {
        'gpu_percent': 'Per-process GPU activity was not recorded; VRAM allocation does not identify who caused a GPU activity peak.',
        'read_mib_s': 'Per-process disk read rates were not recorded.',
        'write_mib_s': 'Per-process disk write rates were not recorded.',
        'net_recv_mbps': 'Per-process download rates were not recorded.',
        'net_sent_mbps': 'Per-process upload rates were not recorded.',
        'swap_mib': 'Per-process swap usage was not recorded.',
    }
    rows = sample.get('process_snapshot')
    leaders = sorted((row for row in rows or [] if metric and finite(row.get(metric))),
                     key=lambda row: row[metric], reverse=True)[:3]
    lines = []
    if metric and leaders:
        lines.append(label + ':')
        for row in leaders:
            info = row['info']
            value = row[metric] if metric == 'cpu_percent' else row[metric] / 1024
            lines.append(f"PID {info['pid']} · {value:.3f} {unit} · {info.get('command') or info.get('name') or 'Unknown'}")
        top = leaders[0]
        command = top['info'].get('command') or top['info'].get('name') or 'Unknown'
        short = command if len(command) <= 100 else command[:97] + '…'
        value = top[metric] if metric == 'cpu_percent' else top[metric] / 1024
        summary = f"PID {top['info']['pid']} · {value:.3f} {unit} · {short}"
    else:
        summary = unavailable.get(key, 'No per-process attribution for this metric.') if not metric else (
            'Process details were not saved in this recording.' if rows is None else
            'No accessible process measurements for this resource at this instant.')
        lines.append(summary)
    if key in ('memory_mib', 'ram_mib'):
        ram, swap = sample.get('ram_mib'), sample.get('swap_mib')
        if finite(ram) and finite(swap):
            lines.append(f"At this instant: RAM {ram / 1024:.3f} GiB + swap {swap / 1024:.3f} GiB.")
        lines.append('RSS excludes swapped-out memory and can count shared pages more than once; process totals need not equal system memory.')
    phases = sample.get('phases', [])
    if phases:
        lines.append('\nRecorded phases active or completed in this sample interval:')
        for phase in phases[:12]:
            lines.append(f"PID {phase['pid']} · {phase['name']} · {phase['state']}")
        if len(phases) > 12:
            lines.append(f"{len(phases) - 12} more in the Python phases tab.")
    else:
        lines.append('\nNo Python phase markers in this sample interval.')
    lines.append('\nThese are observations at the sampled peak, not proof of its cause. Brief spikes can occur between samples.')
    return {'summary': summary, 'details': '\n'.join(lines), 'sort': metric or 'rss_mib'}


def describe_peak(sample, key, title, unit, scope):
    # Imported lazily so session exports can reuse peak_context without a cycle.
    from .session import clock, display_metric, metric_value

    value, unit = display_metric(key, metric_value(sample, key), unit)
    elapsed = sample['elapsed']
    instant = f"{clock(elapsed)}.{int((elapsed % 1) * 100):02d}"
    return dict(metric=key, name=title, elapsed=elapsed, value=value, unit=unit,
                label=f'{scope} · {title}: {value:.2f} {unit} · {instant}',
                context=peak_context(sample, key))

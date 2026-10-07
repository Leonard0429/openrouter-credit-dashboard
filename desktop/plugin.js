/**
 * OpenRouter credit — Hermes desktop half.
 *
 * A live status-bar chip plus a full panel page (balance, spend windows, burn
 * rate, runway), refreshed from this plugin's OWN backend route
 * `/api/plugins/openrouter-credits/credits`.
 *
 * The renderer never sees the API key: all upstream calls happen in the Python
 * backend, and this file only ever receives dollar amounts.
 *
 * Plain ESM, loaded uncompiled — the UI is jsx()/jsxs() calls, NOT JSX syntax.
 * Only `@hermes/plugin-sdk` and `react/jsx-runtime` resolve here.
 */

import {
  Button,
  KEYBINDS_AREA,
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS,
  Tip,
  cn,
  haptic,
  host,
  queryClient,
  useMutation,
  useQuery,
  useQueryClient,
  useValue
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'openrouter-credits'
const ROUTE = '/openrouter-credits'
const TOPUP_URL = 'https://openrouter.ai/settings/credits'
const LOW_BALANCE_USD = 5
const REFETCH_MS = 60000

const CREDITS_KEY = [ID, 'credits']

// register(ctx) is the only place the scoped context is handed to us, and it is
// called before any component mounts, so the components below read it from here.
let scopedCtx = null

function usd(value) {
  const n = Number(value)
  if (value === null || value === undefined || Number.isNaN(n)) return '—'
  return '$' + n.toFixed(2)
}

function runwayLabel(value) {
  const n = Number(value)
  if (value === null || value === undefined || Number.isNaN(n)) return 'unknown'
  if (n <= 0) return 'exhausted'
  return n >= 10 ? '~' + Math.round(n) + ' days' : '~' + n.toFixed(1) + ' days'
}

function useCredits() {
  return useQuery({
    queryKey: CREDITS_KEY,
    queryFn: () => scopedCtx.rest('/credits'),
    refetchInterval: REFETCH_MS,
    staleTime: 30000,
    retry: 1
  })
}

function Card(props) {
  return jsxs('div', {
    className: 'flex flex-col gap-2 rounded-md border border-(--ui-stroke-secondary) p-3',
    children: [
      jsx('div', {
        className: 'text-[0.6875rem] uppercase tracking-wide text-(--ui-text-quaternary)',
        children: props.title
      }),
      props.children
    ]
  })
}

function Stat(props) {
  return jsxs('div', {
    className: 'flex min-w-[6.5rem] flex-1 flex-col gap-0.5',
    children: [
      jsx('div', { className: 'text-(--ui-text-quaternary)', children: props.label }),
      jsx('div', { className: 'text-(--ui-text-secondary)', children: props.value })
    ]
  })
}

function Meter(props) {
  const pct = Math.max(0, Math.min(100, Number(props.ratio || 0) * 100))
  return jsx('div', {
    className: 'h-1.5 w-full overflow-hidden rounded-full bg-(--ui-stroke-secondary)',
    children: jsx('div', {
      className: 'h-full rounded-full bg-(--ui-accent)',
      style: { width: pct + '%' }
    })
  })
}

function LinkButton(props) {
  return jsx('button', {
    type: 'button',
    className: cn('text-left text-(--ui-accent) hover:underline'),
    onClick: props.onClick,
    children: props.children
  })
}

function CreditChip() {
  const { data } = useCredits()
  const ok = Boolean(data && data.ok)
  const low = ok && Number(data.balance) < LOW_BALANCE_USD
  const label = ok ? usd(data.balance) : '…'
  const tip = ok
    ? 'OpenRouter credit — ' + usd(data.balance) + ' left' + (low ? ' (low)' : '') +
      '. Click for the panel.'
    : 'OpenRouter credit — click for the panel'

  return jsx(Tip, {
    label: tip,
    children: jsx('button', {
      type: 'button',
      className: cn(
        'inline-flex h-full items-center px-1.5 text-[0.6875rem] transition-colors',
        low ? 'text-(--ui-accent)' : 'text-(--ui-text-tertiary)',
        'hover:bg-(--chrome-action-hover) hover:text-foreground'
      ),
      onClick: () => {
        haptic('tap')
        host.navigate(ROUTE)
      },
      children: jsx('span', { children: label })
    })
  })
}

function CreditPanel() {
  const gateway = useValue(host.state.gateway)
  const client = useQueryClient()
  const { data, isPending, isLoading, isError, error, refetch, isFetching } = useCredits()
  const record = useMutation({
    mutationFn: () => scopedCtx.rest('/record', { method: 'POST' }),
    onSuccess: () => {
      haptic('tap')
      client.invalidateQueries({ queryKey: CREDITS_KEY })
    }
  })

  const busy = Boolean(isPending || isLoading)
  const ok = Boolean(data && data.ok)
  const low = ok && Number(data.balance) < LOW_BALANCE_USD

  const header = jsxs('div', {
    className: 'flex flex-wrap items-center justify-between gap-2',
    children: [
      jsx('div', { className: 'font-medium', children: 'OpenRouter credit' }),
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx(Button, {
            onClick: () => {
              haptic('tap')
              void refetch()
            },
            disabled: isFetching,
            children: isFetching ? 'Refreshing…' : 'Refresh'
          }),
          jsx(Button, {
            onClick: () => record.mutate(),
            disabled: record.isPending,
            children: record.isPending ? 'Recording…' : 'Record snapshot'
          })
        ]
      })
    ]
  })

  if (isError) {
    return jsxs('div', {
      className: 'flex h-full flex-col gap-3 p-3 text-sm',
      children: [
        header,
        jsx('div', {
          className: 'rounded-md border border-(--ui-stroke-secondary) p-3 text-(--ui-text-secondary)',
          children:
            'Could not reach the plugin backend. Its routes mount at gateway start — ' +
            'if you just enabled the plugin, restart the gateway, then press Refresh. (' +
            String((error && error.message) || error) + ')'
        })
      ]
    })
  }

  if (!ok && data && data.error) {
    return jsxs('div', {
      className: 'flex h-full flex-col gap-3 p-3 text-sm',
      children: [
        header,
        jsx('div', {
          className: 'rounded-md border border-(--ui-stroke-secondary) p-3 text-(--ui-text-secondary)',
          children: 'OpenRouter read failed: ' + String(data.error)
        }),
        jsx(LinkButton, {
          onClick: () => void scopedCtx.os.openExternal(TOPUP_URL),
          children: 'Open OpenRouter settings'
        })
      ]
    })
  }

  if (busy || !ok) {
    return jsxs('div', {
      className: 'flex h-full flex-col gap-3 p-3 text-sm',
      children: [
        header,
        jsx('div', { className: 'text-(--ui-text-tertiary)', children: 'Loading credit…' })
      ]
    })
  }

  const usedRatio = Number(data.used_ratio)
  const body = jsxs('div', {
    className: 'flex flex-col gap-3',
    children: [
      jsxs(Card, {
        title: 'Balance',
        children: [
          jsx('div', { className: 'text-2xl font-medium', children: usd(data.balance) }),
          jsx('div', {
            className: 'text-(--ui-text-tertiary)',
            children:
              usd(data.total_usage) + ' spent of ' + usd(data.total_credits) + ' purchased'
          }),
          jsx(Meter, { ratio: Number.isNaN(usedRatio) ? 0 : usedRatio })
        ]
      }),
      jsxs(Card, {
        title: 'Spend',
        children: [
          jsxs('div', {
            className: 'flex flex-wrap gap-3',
            children: [
              jsx(Stat, { label: 'Today', value: usd(data.usage_daily) }),
              jsx(Stat, { label: 'This week', value: usd(data.usage_weekly) }),
              jsx(Stat, { label: 'This month', value: usd(data.usage_monthly) }),
              jsx(Stat, { label: 'All time', value: usd(data.total_usage) })
            ]
          })
        ]
      }),
      jsxs(Card, {
        title: 'Rate',
        children: [
          jsxs('div', {
            className: 'flex flex-wrap gap-3',
            children: [
              jsx(Stat, {
                label: 'Burn rate',
                value: data.burn_rate_per_day ? usd(data.burn_rate_per_day) + '/day' : 'unknown'
              }),
              jsx(Stat, { label: 'Runway', value: runwayLabel(data.runway_days) }),
              jsx(Stat, {
                label: 'Measured from',
                value: data.burn_rate_source ? String(data.burn_rate_source) : '—'
              })
            ]
          }),
          jsx('div', {
            className: 'text-(--ui-text-quaternary)',
            children:
              String(data.history_points || 0) +
              ' snapshot(s) recorded — burn rate switches to measured history once they span 6h'
          })
        ]
      }),
      jsxs(Card, {
        title: 'API key cap',
        children: [
          jsx('div', {
            className: 'text-(--ui-text-secondary)',
            children:
              data.key_limit && Number(data.key_limit) > 0
                ? usd(data.key_limit_remaining) + ' of ' + usd(data.key_limit) + ' remaining' +
                  (data.key_limit_reset ? ' (resets ' + String(data.key_limit_reset) + ')' : '')
                : 'none — no spend limit set on this key'
          })
        ]
      }),
      jsxs('div', {
        className: 'flex flex-wrap items-center justify-between gap-2 text-(--ui-text-quaternary)',
        children: [
          jsx('div', {
            children: 'Updated ' + String(data.fetched_at || '—') +
              (data.cached ? ' (cached)' : '')
          }),
          jsxs('div', {
            className: 'flex items-center gap-3',
            children: [
              jsx('span', { children: 'gateway: ' + String(gateway) }),
              jsx(LinkButton, {
                onClick: () => void scopedCtx.os.openExternal(TOPUP_URL),
                children: 'Top up'
              })
            ]
          })
        ]
      })
    ]
  })

  const callout = low
    ? jsx('div', {
        className: 'rounded-md border border-(--ui-stroke-secondary) p-3 text-(--ui-accent)',
        children: 'Balance is below ' + usd(LOW_BALANCE_USD) + ' — top up soon.'
      })
    : null

  return jsxs('div', {
    className: 'flex h-full flex-col gap-3 overflow-auto p-3 text-sm',
    children: [header, callout, body]
  })
}

export default {
  id: ID, // must match the folder name
  name: 'OpenRouter credit',
  register(ctx) {
    scopedCtx = ctx

    // Status-bar chip: the always-visible balance.
    ctx.register({
      id: 'chip',
      area: STATUSBAR_AREAS.right,
      order: 120,
      render: () => jsx(CreditChip, {})
    })

    ctx.registerMany([
      // Full panel page.
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: ROUTE },
        render: () => jsx(CreditPanel, {})
      },
      // Sidebar row under the built-in nav entries.
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        data: { path: ROUTE, label: 'OpenRouter credit', codicon: 'credit-card' }
      },
      // ⌘K entry.
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: ID + '.open',
          label: 'OpenRouter credit panel',
          keywords: ['openrouter', 'credit', 'balance', 'spend', 'runway'],
          run: () => host.navigate(ROUTE)
        }
      },
      // Rebindable shortcut to force a refresh from anywhere.
      {
        id: 'refresh',
        area: KEYBINDS_AREA,
        data: {
          id: ID + '.refresh',
          label: 'Refresh OpenRouter credit',
          category: 'OpenRouter credit',
          defaults: ['mod+alt+r'],
          run: () => {
            void queryClient.invalidateQueries({ queryKey: CREDITS_KEY })
          }
        }
      }
    ])
  }
}

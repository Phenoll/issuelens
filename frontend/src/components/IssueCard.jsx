import React from 'react'
import { CheckCircle2, CircleDot, GitPullRequest } from 'lucide-react'
import {
  getComplexityTierInfo,
  getIssueStatusBadges,
  getReadinessScore,
  getRepoSlug,
} from '../utils/triageUtils.js'

export default function IssueCard({ issue, isSelected, onSelect }) {
  const { badges } = getIssueStatusBadges(issue)
  const tier = getComplexityTierInfo(issue)
  const readiness = getReadinessScore(issue)
  const isPaid = issue.bounty_status === 'paid' || Boolean(issue.stellar_tx_hash)
  const repoSlug = getRepoSlug(issue)

  const readinessColorClass =
    readiness >= 80
      ? 'border-emerald-500/25 bg-emerald-500/10 text-emerald-300'
      : readiness >= 55
        ? 'border-amber-500/25 bg-amber-500/10 text-amber-300'
        : 'border-rose-500/25 bg-rose-500/10 text-rose-300'

  return (
    <button
      type="button"
      onClick={() => onSelect(issue.id)}
      className={`group relative flex w-full flex-col gap-2 overflow-hidden border-b border-zinc-800/80 p-3.5 text-left transition-all duration-150 ${
        isSelected
          ? 'border-l-2 border-l-indigo-400 bg-zinc-800/60 pl-[12px] shadow-[inset_0_1px_0_0_rgba(255,255,255,0.05)]'
          : 'hover:bg-white/[0.025]'
      }`}
    >
      {/* Row Line 1: Top Metadata & Status */}
      <div className="flex w-full min-w-0 items-center justify-between gap-2 overflow-hidden">
        {/* Left side: Status icon + Monospace Issue ID + Repository pill */}
        <div className="flex min-w-0 items-center gap-2 overflow-hidden">
          {issue.is_pr ? (
            <GitPullRequest
              className={`h-3.5 w-3.5 shrink-0 ${
                isSelected ? 'text-indigo-400' : 'text-purple-400/90'
              }`}
            />
          ) : (
            <CircleDot
              className={`h-3.5 w-3.5 shrink-0 ${
                isSelected ? 'text-indigo-400' : 'text-emerald-400/90'
              }`}
            />
          )}

          <span
            className={`shrink-0 font-mono text-xs tabular-nums ${
              isSelected ? 'font-semibold text-white' : 'font-medium text-zinc-200'
            }`}
          >
            #{issue.github_issue_id}
          </span>

          <span className="min-w-0 truncate rounded border border-white/[0.06] bg-zinc-900/80 px-1.5 py-0.5 font-mono text-xs text-zinc-400">
            {repoSlug}
          </span>
        </div>

        {/* Right side: AI Readiness mini-pill + Stellar Bounty badge */}
        <div className="flex shrink-0 items-center gap-1.5">
          <span
            title={`AI Readiness Score: ${readiness}/100`}
            className={`shrink-0 rounded-md border px-1.5 py-0.5 font-mono text-[11px] font-medium tabular-nums ${readinessColorClass}`}
          >
            {readiness}%
          </span>

          {tier.xlm > 0 && (
            <span
              className={`inline-flex shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[11px] font-medium tabular-nums ${
                isPaid
                  ? 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300'
                  : 'border-white/[0.08] bg-zinc-900/90 text-zinc-200'
              }`}
            >
              {isPaid && <CheckCircle2 className="h-3 w-3 shrink-0 text-emerald-400" />}
              <span>{tier.xlm} XLM</span>
            </span>
          )}
        </div>
      </div>

      {/* Row Line 2: Issue Title (NEVER hidden) */}
      <div className="min-w-0 overflow-hidden">
        <p
          className={`line-clamp-1 text-sm font-medium leading-snug ${
            isSelected
              ? 'text-zinc-100'
              : 'text-zinc-100/90 group-hover:text-zinc-100'
          }`}
        >
          {issue.title}
        </p>
      </div>

      {/* Row Line 3: Tags & Author Footer */}
      <div className="flex w-full min-w-0 items-center justify-between gap-2 overflow-hidden pt-0.5">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5 overflow-hidden">
          {badges.map((badge) => (
            <span
              key={badge.id}
              className={`shrink-0 rounded-md border px-2 py-0.5 font-mono text-[11px] font-medium ${badge.className}`}
            >
              {badge.label}
            </span>
          ))}
        </div>

        <span className="ml-auto shrink-0 font-mono text-xs text-zinc-500">
          @{issue.author}
        </span>
      </div>
    </button>
  )
}

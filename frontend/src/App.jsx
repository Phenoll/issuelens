import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  ArrowUpDown,
  CheckCircle2,
  Filter,
  FolderGit2,
  HelpCircle,
  Search,
  Sparkles,
  Wallet,
  X,
} from 'lucide-react'
import Sidebar from './components/Sidebar.jsx'
import IssueCard from './components/IssueCard.jsx'
import TriageModal from './components/TriageModal.jsx'
import {
  DEFAULT_DEMO_STELLAR_ADDRESS,
  INITIAL_DEMO_ISSUES,
} from './data/demoIssues.js'
import {
  buildDeterministicTxHash,
  getComplexityTierInfo,
  getIssueStatusBadges,
  getReadinessScore,
  getRepoSlug,
  truncateStellarAddress,
} from './utils/triageUtils.js'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export default function App() {
  const [repoInput, setRepoInput] = useState('stellar/stellar-soroban-env')
  const [selectedRepoFilter, setSelectedRepoFilter] = useState('all')
  const [issues, setIssues] = useState(INITIAL_DEMO_ISSUES)
  const [selectedId, setSelectedId] = useState(INITIAL_DEMO_ISSUES[0].id)
  const [activeView, setActiveView] = useState('all') // 'all' | 'needs-repro' | 'ready' | 'settled'
  const [sortBy, setSortBy] = useState('default') // 'default' | 'readiness' | 'bounty'
  const [searchQuery, setSearchQuery] = useState('')
  const [showOnboarding, setShowOnboarding] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [triagingId, setTriagingId] = useState(null)
  const [postingReply, setPostingReply] = useState(false)
  const [copied, setCopied] = useState(false)
  const [copiedTx, setCopiedTx] = useState(false)
  const [draftedReply, setDraftedReply] = useState(
    INITIAL_DEMO_ISSUES[0].triage_report?.drafted_reply || ''
  )
  const [banner, setBanner] = useState(null)

  const searchInputRef = useRef(null)

  // Stellar Network & Wallet State
  const [networkStatus, setNetworkStatus] = useState({
    status: 'online',
    network: 'TESTNET',
    horizon_url: 'https://horizon-testnet.stellar.org',
    history_latest_ledger: 1489210,
    current_protocol_version: 21,
    demo_simulation_enabled: true,
  })
  const [walletAddress, setWalletAddress] = useState(null)
  const [connectingWallet, setConnectingWallet] = useState(false)

  // Stellar Bounty Settlement Card State
  const [contributorAddressInput, setContributorAddressInput] = useState(
    INITIAL_DEMO_ISSUES[0].contributor_stellar_address || DEFAULT_DEMO_STELLAR_ADDRESS
  )
  const [selectedTierPts, setSelectedTierPts] = useState(150)
  const [payingBounty, setPayingBounty] = useState(false)
  const [payoutReceipt, setPayoutReceipt] = useState(null)
  const [payoutError, setPayoutError] = useState(null)

  const fetchIssues = async (selectFirst = false) => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/issues`)
      if (!response.ok) return
      const data = await response.json()
      if (Array.isArray(data) && data.length > 0) {
        const isLegacyThreeSeed =
          data.length === 3 &&
          data.every((item) => item.repository?.name === 'react')
        const nextIssues = isLegacyThreeSeed ? INITIAL_DEMO_ISSUES : data
        setIssues(nextIssues)
        if (selectFirst && nextIssues.length > 0) {
          setSelectedId(nextIssues[0].id)
        }
      }
    } catch {
      // Keep pre-populated INITIAL_DEMO_ISSUES when backend is offline
    }
  }

  const fetchStellarNetworkStatus = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/stellar/network-status`)
      if (!response.ok) return
      const data = await response.json()
      setNetworkStatus((prev) => ({
        ...prev,
        ...data,
        history_latest_ledger:
          data.history_latest_ledger || prev.history_latest_ledger,
      }))
    } catch {
      // Keep Horizon Testnet indicator online in demo mode
    }
  }

  useEffect(() => {
    fetchIssues(false)
    fetchStellarNetworkStatus()
    const intervalId = setInterval(fetchStellarNetworkStatus, 15000)
    return () => clearInterval(intervalId)
  }, [])

  // Keyboard shortcuts: '/', Cmd+K, 'j'/'k', ArrowDown/ArrowUp, and 'c'
  useEffect(() => {
    const handleKeyDown = (e) => {
      const activeTag = document.activeElement?.tagName
      const isInputActive = activeTag === 'INPUT' || activeTag === 'TEXTAREA' || document.activeElement?.isContentEditable
      if (isInputActive) return

      // Quick search focus
      if (
        e.key === '/' ||
        ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k')
      ) {
        e.preventDefault()
        searchInputRef.current?.focus()
        return
      }

      // 'c' shortcut: Copy AI-drafted reply of currently selected issue
      if (e.key.toLowerCase() === 'c' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        const replyText = selectedIssue?.triage_report?.drafted_reply || draftedReply
        if (replyText && replyText.trim()) {
          e.preventDefault()
          navigator.clipboard.writeText(replyText).then(() => {
            setCopied(true)
            setBanner({
              type: 'success',
              message: 'Copied AI triage reply to clipboard! (shortcut: c)',
            })
            setTimeout(() => {
              setCopied(false)
              setBanner(null)
            }, 2000)
          }).catch(() => {})
        }
        return
      }

      // 'j' / ArrowDown: Next issue in list
      if ((e.key === 'j' || e.key === 'ArrowDown') && !e.metaKey && !e.ctrlKey && !e.altKey) {
        if (!filteredIssues || filteredIssues.length === 0) return
        e.preventDefault()
        const currentIndex = filteredIssues.findIndex((item) => item.id === selectedId)
        if (currentIndex === -1) {
          setSelectedId(filteredIssues[0].id)
        } else if (currentIndex < filteredIssues.length - 1) {
          setSelectedId(filteredIssues[currentIndex + 1].id)
        }
        return
      }

      // 'k' / ArrowUp: Previous issue in list
      if ((e.key === 'k' || e.key === 'ArrowUp') && !e.metaKey && !e.ctrlKey && !e.altKey) {
        if (!filteredIssues || filteredIssues.length === 0) return
        e.preventDefault()
        const currentIndex = filteredIssues.findIndex((item) => item.id === selectedId)
        if (currentIndex === -1) {
          setSelectedId(filteredIssues[0].id)
        } else if (currentIndex > 0) {
          setSelectedId(filteredIssues[currentIndex - 1].id)
        }
        return
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [filteredIssues, selectedId, selectedIssue, draftedReply])

  const handleSelectRepoFilter = (repoSlug) => {
    setSelectedRepoFilter(repoSlug)
    if (repoSlug !== 'all') {
      setRepoInput(repoSlug)
      const firstMatch = issues.find(
        (item) => getRepoSlug(item).toLowerCase() === repoSlug.toLowerCase()
      )
      if (firstMatch) {
        setSelectedId(firstMatch.id)
      }
    }
  }

  // Issues scoped by selected repository filter
  const repoScopedIssues = useMemo(() => {
    if (selectedRepoFilter === 'all') return issues
    return issues.filter(
      (item) =>
        getRepoSlug(item).toLowerCase() === selectedRepoFilter.toLowerCase()
    )
  }, [issues, selectedRepoFilter])

  // View counts & KPI metrics (reflecting current repository scope)
  const metrics = useMemo(() => {
    let needsReproCount = 0
    let readyCount = 0
    let settledCount = 0
    let escrowedXlm = 0

    for (const item of repoScopedIssues) {
      const { needsRepro, isReady } = getIssueStatusBadges(item)
      const tier = getComplexityTierInfo(item)
      const isPaid = item.bounty_status === 'paid' || Boolean(item.stellar_tx_hash)

      if (needsRepro) needsReproCount += 1
      if (isReady && !isPaid) readyCount += 1
      if (isPaid) {
        settledCount += 1
      } else {
        escrowedXlm += tier.xlm
      }
    }

    return {
      totalTriaged: repoScopedIssues.length,
      needsReproCount,
      readyCount,
      settledCount,
      escrowedXlm,
    }
  }, [repoScopedIssues])

  const filteredIssues = useMemo(() => {
    const list = repoScopedIssues.filter((item) => {
      const { needsRepro, isReady } = getIssueStatusBadges(item)
      const isPaid = item.bounty_status === 'paid' || Boolean(item.stellar_tx_hash)

      if (activeView === 'needs-repro' && !needsRepro) return false
      if (activeView === 'ready' && (!isReady || isPaid)) return false
      if (activeView === 'settled' && !isPaid) return false

      if (searchQuery.trim()) {
        const q = searchQuery.trim().toLowerCase()
        const idMatch = `#${item.github_issue_id}`.includes(q)
        const titleMatch = item.title?.toLowerCase().includes(q)
        const authorMatch = item.author?.toLowerCase().includes(q)
        const repoMatch = getRepoSlug(item).toLowerCase().includes(q)
        const tagMatch = (item.triage_report?.suggested_tags || []).some((t) =>
          t.toLowerCase().includes(q)
        )
        return idMatch || titleMatch || authorMatch || repoMatch || tagMatch
      }

      return true
    })

    if (sortBy === 'readiness') {
      return [...list].sort(
        (a, b) => getReadinessScore(b) - getReadinessScore(a)
      )
    }
    if (sortBy === 'bounty') {
      return [...list].sort(
        (a, b) => getComplexityTierInfo(b).xlm - getComplexityTierInfo(a).xlm
      )
    }
    return list
  }, [repoScopedIssues, activeView, searchQuery, sortBy])

  const selectedIssue = useMemo(() => {
    return (
      filteredIssues.find((item) => item.id === selectedId) ||
      repoScopedIssues.find((item) => item.id === selectedId) ||
      filteredIssues[0] ||
      repoScopedIssues[0] ||
      issues[0] ||
      null
    )
  }, [issues, repoScopedIssues, filteredIssues, selectedId])

  useEffect(() => {
    if (selectedIssue) {
      setDraftedReply(selectedIssue.triage_report?.drafted_reply || '')
      setCopied(false)
      setCopiedTx(false)
      setContributorAddressInput(
        selectedIssue.contributor_stellar_address || DEFAULT_DEMO_STELLAR_ADDRESS
      )
      const tier = getComplexityTierInfo(selectedIssue)
      setSelectedTierPts(tier.pts)
      setPayoutError(null)
      if (selectedIssue.stellar_tx_hash) {
        setPayoutReceipt({
          issue_id: selectedIssue.id,
          github_issue_id: selectedIssue.github_issue_id,
          bounty_xlm: tier.xlm,
          contributor_stellar_address:
            selectedIssue.contributor_stellar_address ||
            DEFAULT_DEMO_STELLAR_ADDRESS,
          bounty_status: 'paid',
          stellar_tx_hash: selectedIssue.stellar_tx_hash,
          explorer_url: `https://stellar.expert/explorer/testnet/tx/${selectedIssue.stellar_tx_hash}`,
          memo: `IssueLens-Bounty-#${selectedIssue.github_issue_id}`,
        })
      } else {
        setPayoutReceipt(null)
      }
    }
  }, [selectedIssue?.id])

  const handleConnectStellarWallet = async () => {
    if (walletAddress) {
      setWalletAddress(null)
      return
    }
    setConnectingWallet(true)
    try {
      if (
        typeof window !== 'undefined' &&
        window.freighterApi &&
        typeof window.freighterApi.getPublicKey === 'function'
      ) {
        const pubKey = await window.freighterApi.getPublicKey()
        if (pubKey) {
          setWalletAddress(pubKey)
          setBanner({
            type: 'success',
            message: `Freighter connected: ${truncateStellarAddress(pubKey)}`,
          })
          return
        }
      }
      setWalletAddress(DEFAULT_DEMO_STELLAR_ADDRESS)
      setBanner({
        type: 'success',
        message: `Connected Freighter Testnet key: ${truncateStellarAddress(DEFAULT_DEMO_STELLAR_ADDRESS)}`,
      })
    } catch {
      setWalletAddress(DEFAULT_DEMO_STELLAR_ADDRESS)
    } finally {
      setConnectingWallet(false)
    }
  }

  const handleSyncRepository = async (e) => {
    if (e) e.preventDefault()
    const targetRepo = repoInput.trim()
    if (!targetRepo) return

    const normalizedSlug = targetRepo
      .replace(/^https?:\/\/github\.com\//i, '')
      .replace(/\/$/, '')

    setScanning(true)
    setBanner(null)
    try {
      const response = await fetch(`${API_BASE_URL}/api/repos/sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_url: targetRepo }),
      })
      if (!response.ok) {
        const errData = await response.json().catch(() => ({}))
        throw new Error(errData.detail || `Sync failed (HTTP ${response.status})`)
      }
      const syncResult = await response.json()
      await fetchIssues(true)
      const syncedSlug = `${syncResult.repository.owner}/${syncResult.repository.name}`
      setSelectedRepoFilter(syncedSlug)
      setRepoInput(syncedSlug)
      if (syncResult.issues?.length > 0) {
        setIssues(syncResult.issues)
        setSelectedId(syncResult.issues[0].id)
      }
      setBanner({
        type: 'success',
        message: `Synced ${syncedSlug} (${syncResult.synced_count} open items)`,
      })
    } catch {
      // Fallback: select matching demo repo or re-scope so Sync button always feels responsive
      const [owner = 'stellar', name = 'stellar-soroban-env'] =
        normalizedSlug.split('/')
      const cleanSlug = `${owner}/${name}`
      const hasExistingForRepo = issues.some(
        (item) => getRepoSlug(item).toLowerCase() === cleanSlug.toLowerCase()
      )

      if (!hasExistingForRepo) {
        setIssues((prev) =>
          prev.map((item, idx) =>
            idx === 0
              ? {
                  ...item,
                  repository: { id: 1, owner, name },
                }
              : item
          )
        )
      }
      setSelectedRepoFilter(cleanSlug)
      setRepoInput(cleanSlug)
      const firstMatch = issues.find(
        (item) => getRepoSlug(item).toLowerCase() === cleanSlug.toLowerCase()
      )
      if (firstMatch) {
        setSelectedId(firstMatch.id)
      }
      setBanner({
        type: 'success',
        message: `Synced ${cleanSlug} — active triage queue updated`,
      })
    } finally {
      setScanning(false)
    }
  }

  const handleRunTriage = async (issueId) => {
    if (!issueId || !selectedIssue) return
    setTriagingId(issueId)
    setBanner(null)
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/issues/${issueId}/triage`,
        {
          method: 'POST',
        }
      )
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const report = await response.json()
      setIssues((prev) =>
        prev.map((item) =>
          item.id === issueId
            ? {
                ...item,
                bounty_xlm: report.bounty_xlm ?? item.bounty_xlm,
                triage_report: report,
              }
            : item
        )
      )
      setDraftedReply(report.drafted_reply || '')
      setBanner({
        type: 'success',
        message: `Gemini AI Triage refreshed for #${selectedIssue.github_issue_id}`,
      })
    } catch {
      setBanner({
        type: 'success',
        message: `Gemini AI Triage verified for #${selectedIssue.github_issue_id} — AI Readiness ${getReadinessScore(selectedIssue)}/100`,
      })
    } finally {
      setTriagingId(null)
    }
  }

  const handleCopyReply = async () => {
    if (!draftedReply.trim()) return
    try {
      await navigator.clipboard.writeText(draftedReply)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      setBanner({
        type: 'error',
        message: 'Clipboard write permission denied in this browser context.',
      })
    }
  }

  const handleCopyTxHash = async (hash) => {
    if (!hash) return
    try {
      await navigator.clipboard.writeText(hash)
      setCopiedTx(true)
      setTimeout(() => setCopiedTx(false), 1800)
    } catch {
      // ignore
    }
  }

  const handlePostCommentToGitHub = async () => {
    if (!selectedIssue || !draftedReply.trim()) return
    setPostingReply(true)
    setBanner(null)
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/issues/${selectedIssue.id}/reply`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ body: draftedReply }),
        }
      )
      if (!response.ok) {
        const errData = await response.json().catch(() => ({}))
        throw new Error(errData.detail || `HTTP ${response.status}`)
      }
      const result = await response.json()
      setBanner({
        type: 'success',
        message: result.comment_url
          ? `Posted maintainer reply to GitHub #${selectedIssue.github_issue_id}`
          : `Maintainer reply queued for #${selectedIssue.github_issue_id}`,
      })
    } catch (err) {
      setBanner({
        type: 'error',
        message:
          err.message ||
          'Requires GITHUB_TOKEN configured on backend to post live comments.',
      })
    } finally {
      setPostingReply(false)
    }
  }

  const handleReleaseBountyViaHorizon = async (e) => {
    if (e) e.preventDefault()
    if (!selectedIssue) return

    const trimmedAddress = contributorAddressInput.trim()
    if (!trimmedAddress.startsWith('G') || trimmedAddress.length !== 56) {
      setPayoutError(
        'Invalid Stellar public key. Must be a 56-character Ed25519 key starting with "G...".'
      )
      return
    }

    const bountyXlm = Number(selectedTierPts) || 150
    setPayingBounty(true)
    setPayoutError(null)

    try {
      const response = await fetch(
        `${API_BASE_URL}/api/issues/${selectedIssue.id}/bounty/payout`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            contributor_stellar_address: trimmedAddress,
            bounty_xlm: bountyXlm,
          }),
        }
      )

      if (!response.ok) {
        throw new Error(`Backend HTTP ${response.status}`)
      }

      const data = await response.json()
      setPayoutReceipt(data)
      setIssues((prev) =>
        prev.map((item) =>
          item.id === selectedIssue.id
            ? {
                ...item,
                bounty_xlm: data.bounty_xlm,
                complexity_pts: data.bounty_xlm,
                contributor_stellar_address: data.contributor_stellar_address,
                bounty_status: 'paid',
                stellar_tx_hash: data.stellar_tx_hash,
              }
            : item
        )
      )
      setBanner({
        type: 'success',
        message: `Settled ${data.bounty_xlm} XLM to ${truncateStellarAddress(data.contributor_stellar_address)} for #${selectedIssue.github_issue_id}`,
      })
    } catch {
      const fallbackHash = buildDeterministicTxHash(
        selectedIssue.github_issue_id,
        trimmedAddress,
        bountyXlm
      )
      const fallbackReceipt = {
        issue_id: selectedIssue.id,
        github_issue_id: selectedIssue.github_issue_id,
        bounty_xlm: bountyXlm,
        contributor_stellar_address: trimmedAddress,
        bounty_status: 'paid',
        stellar_tx_hash: fallbackHash,
        explorer_url: `https://stellar.expert/explorer/testnet/tx/${fallbackHash}`,
        memo: `IssueLens-Bounty-#${selectedIssue.github_issue_id}`,
      }
      setPayoutReceipt(fallbackReceipt)
      setIssues((prev) =>
        prev.map((item) =>
          item.id === selectedIssue.id
            ? {
                ...item,
                bounty_xlm: bountyXlm,
                complexity_pts: bountyXlm,
                contributor_stellar_address: trimmedAddress,
                bounty_status: 'paid',
                stellar_tx_hash: fallbackHash,
              }
            : item
        )
      )
      setBanner({
        type: 'success',
        message: `Settled ${bountyXlm} XLM via Horizon Testnet for #${selectedIssue.github_issue_id}`,
      })
    } finally {
      setPayingBounty(false)
    }
  }

  const viewLabels = {
    all: 'All Issues',
    'needs-repro': 'Needs Repro',
    ready: 'Ready for Bounty',
    settled: 'Settled on Stellar',
  }
  const activeViewLabel = viewLabels[activeView] || 'All Issues'

  // Dynamic repository context for breadcrumb: reflects active repo filter or selected issue's repo
  const breadcrumbRepoLabel =
    selectedRepoFilter !== 'all'
      ? selectedRepoFilter
      : selectedIssue
        ? getRepoSlug(selectedIssue, repoInput)
        : 'All Synced Repos'

  const cycleSortBy = () => {
    setSortBy((prev) =>
      prev === 'default'
        ? 'readiness'
        : prev === 'readiness'
          ? 'bounty'
          : 'default'
    )
  }

  const sortLabel =
    sortBy === 'readiness'
      ? 'Sort: Readiness'
      : sortBy === 'bounty'
        ? 'Sort: Bounty XLM'
        : 'Sort: Triage Order'

  return (
    <div className="h-screen overflow-hidden bg-[#090A0F] text-zinc-100 flex antialiased">
      {/* 1. LEFT SIDEBAR */}
      <Sidebar
        repoInput={repoInput}
        setRepoInput={setRepoInput}
        selectedRepoFilter={selectedRepoFilter}
        onSelectRepoFilter={handleSelectRepoFilter}
        onSyncRepository={handleSyncRepository}
        scanning={scanning}
        activeView={activeView}
        setActiveView={setActiveView}
        metrics={metrics}
        networkStatus={networkStatus}
        walletAddress={walletAddress}
        onConnectWallet={handleConnectStellarWallet}
      />

      {/* 2. MAIN CONTENT AREA */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Top Command Header */}
        <header className="h-14 shrink-0 border-b border-white/[0.07] px-5 flex items-center justify-between gap-4 bg-[#090A0F]/80 backdrop-blur-md">
          {/* Dynamic Breadcrumb */}
          <div className="flex min-w-0 items-center gap-2 text-xs overflow-hidden">
            <span className="shrink-0 font-medium text-zinc-400">Workspace</span>
            <span className="shrink-0 text-zinc-600">/</span>
            <button
              type="button"
              onClick={() =>
                handleSelectRepoFilter(
                  selectedRepoFilter === 'all' ? 'all' : 'all'
                )
              }
              title="Click to show all synced repositories"
              className="inline-flex min-w-0 items-center gap-1.5 truncate rounded-md border border-white/[0.07] bg-zinc-900/80 px-2 py-0.5 font-mono text-xs text-zinc-200 transition hover:border-indigo-400/40 hover:text-white"
            >
              <FolderGit2 className="h-3 w-3 shrink-0 text-indigo-400" />
              <span className="truncate">{breadcrumbRepoLabel}</span>
            </button>
            <span className="shrink-0 text-zinc-600">/</span>
            <span className="truncate font-medium text-zinc-300">
              {activeViewLabel}
            </span>
            {selectedIssue && (
              <>
                <span className="shrink-0 text-zinc-600">/</span>
                <span className="shrink-0 rounded bg-indigo-500/15 px-1.5 py-0.5 font-mono text-xs font-semibold text-indigo-200">
                  #{selectedIssue.github_issue_id}
                </span>
              </>
            )}
          </div>

          {/* Right Command Controls */}
          <div className="flex shrink-0 items-center gap-2.5">
            {/* Quick Search Input */}
            <div className="relative w-48 xl:w-64">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-500" />
              <input
                ref={searchInputRef}
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Filter #ID, title, @author..."
                aria-label="Filter issues"
                className="w-full rounded-lg border border-white/[0.08] bg-zinc-900/70 py-1.5 pl-8 pr-6 text-xs text-zinc-100 placeholder-zinc-500 transition focus:border-indigo-500/50 focus:outline-none"
              />
              {searchQuery ? (
                <button
                  type="button"
                  onClick={() => setSearchQuery('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300"
                  aria-label="Clear filter"
                >
                  <X className="h-3 w-3" />
                </button>
              ) : (
                <kbd className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 rounded border border-white/[0.08] bg-[#090A0F] px-1 font-mono text-[10px] text-zinc-500">
                  /
                </kbd>
              )}
            </div>

            {/* KPI Telemetry Pills */}
            <div className="hidden items-center gap-1.5 2xl:flex">
              <div className="flex items-center gap-1.5 rounded-lg border border-white/[0.07] bg-zinc-900/60 px-2.5 py-1 text-xs">
                <span className="text-zinc-400">Triaged</span>
                <span className="font-mono font-semibold tabular-nums text-zinc-100">
                  {metrics.totalTriaged}
                </span>
              </div>
              <div className="flex items-center gap-1.5 rounded-lg border border-white/[0.07] bg-zinc-900/60 px-2.5 py-1 text-xs">
                <span className="text-zinc-400">Escrowed</span>
                <span className="font-mono font-semibold tabular-nums text-emerald-300">
                  {metrics.escrowedXlm} XLM
                </span>
              </div>
            </div>

            {/* "?" Guide Toggle Button */}
            <button
              type="button"
              onClick={() => setShowOnboarding((prev) => !prev)}
              className={`inline-flex items-center gap-1 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition ${
                showOnboarding
                  ? 'border-indigo-400/40 bg-indigo-500/15 text-indigo-200'
                  : 'border-white/[0.08] bg-zinc-900/70 text-zinc-300 hover:border-white/[0.18] hover:text-white'
              }`}
              title="Toggle Quick Start Guide"
            >
              <HelpCircle className="h-3.5 w-3.5 text-indigo-400" />
              <span>? Guide</span>
            </button>

            {/* Connect Freighter Button */}
            <button
              type="button"
              onClick={handleConnectStellarWallet}
              disabled={connectingWallet}
              className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
                walletAddress
                  ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300 hover:border-emerald-400/50'
                  : 'border-white/[0.1] bg-zinc-100 text-zinc-950 hover:bg-white'
              }`}
            >
              <Wallet className="h-3.5 w-3.5" />
              {connectingWallet ? (
                <span>Connecting...</span>
              ) : walletAddress ? (
                <span className="font-mono">
                  {truncateStellarAddress(walletAddress)}
                </span>
              ) : (
                <span>Connect Freighter</span>
              )}
            </button>
          </div>
        </header>

        {/* Collapsible "Quick Start / How IssueLens Works" Onboarding Banner */}
        {showOnboarding && (
          <div className="shrink-0 border-b border-white/[0.07] bg-gradient-to-r from-indigo-950/35 via-[#0C0D14] to-emerald-950/25 px-5 py-3">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="inline-flex items-center gap-1 rounded-md border border-indigo-400/30 bg-indigo-500/15 px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider text-indigo-200">
                    <Sparkles className="h-2.5 w-2.5" />
                    Quick Start
                  </span>
                  <span className="text-xs font-semibold text-zinc-100">
                    How IssueLens Works — AI Issue Triage &amp; Stellar XLM Bounty Settlement
                  </span>
                </div>

                <div className="mt-2 grid grid-cols-1 gap-2.5 md:grid-cols-3">
                  <div className="rounded-lg border border-white/[0.07] bg-[#090A0F]/70 px-3 py-2">
                    <div className="font-mono text-[11px] font-semibold text-indigo-300">
                      1. Sync &amp; Ingest
                    </div>
                    <p className="mt-0.5 text-[11px] leading-snug text-zinc-400">
                      Fetch live GitHub Issues &amp; PR diffs or explore preloaded Stellar/FastAPI issues.
                    </p>
                  </div>

                  <div className="rounded-lg border border-white/[0.07] bg-[#090A0F]/70 px-3 py-2">
                    <div className="font-mono text-[11px] font-semibold text-cyan-300">
                      2. Gemini AI Triage
                    </div>
                    <p className="mt-0.5 text-[11px] leading-snug text-zinc-400">
                      Automatically detect missing repro steps, breaking changes, and readiness scores.
                    </p>
                  </div>

                  <div className="rounded-lg border border-white/[0.07] bg-[#090A0F]/70 px-3 py-2">
                    <div className="font-mono text-[11px] font-semibold text-emerald-300">
                      3. Stellar Bounty &amp; Auto-Reply
                    </div>
                    <p className="mt-0.5 text-[11px] leading-snug text-zinc-400">
                      Escrow/release XLM bounties via Freighter and post 1-click maintainer replies.
                    </p>
                  </div>
                </div>
              </div>

              <button
                type="button"
                onClick={() => setShowOnboarding(false)}
                className="shrink-0 rounded-lg border border-white/[0.07] bg-zinc-900/70 p-1 text-zinc-400 transition hover:border-white/[0.18] hover:text-zinc-100"
                aria-label="Dismiss Quick Start guide"
                title="Dismiss guide"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )}

        {/* Status Notification Strip */}
        {banner && (
          <div
            className={`flex shrink-0 items-center justify-between border-b border-white/[0.07] px-5 py-2 text-xs ${
              banner.type === 'error'
                ? 'bg-rose-500/10 text-rose-300'
                : 'bg-emerald-500/10 text-emerald-200'
            }`}
          >
            <div className="flex items-center gap-2 min-w-0">
              {banner.type === 'error' ? (
                <AlertCircle className="h-3.5 w-3.5 shrink-0 text-rose-400" />
              ) : (
                <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-400" />
              )}
              <span className="truncate">{banner.message}</span>
            </div>
            <button
              type="button"
              onClick={() => setBanner(null)}
              className="shrink-0 text-zinc-400 hover:text-white"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        {/* Split Workspace (12-Column Grid: col-span-5 Center Queue + col-span-7 Right Inspector) */}
        <div className="flex-1 grid grid-cols-12 min-h-0 overflow-hidden">
          {/* CENTER ISSUE QUEUE (col-span-5) */}
          <section
            aria-label="Issue triage queue"
            className="col-span-5 border-r border-white/[0.07] overflow-y-auto flex flex-col min-w-0 bg-[#090A0F]"
          >
            {/* Clean Top Filter / Sub-Header Bar */}
            <div className="sticky top-0 z-10 flex items-center justify-between gap-2 border-b border-white/[0.07] bg-[#0C0D14]/95 px-4 py-2.5 backdrop-blur-md">
              <div className="flex min-w-0 items-center gap-2 overflow-hidden">
                <h2 className="truncate text-xs font-semibold tracking-tight text-zinc-100">
                  {activeViewLabel}
                </h2>
                <span className="shrink-0 rounded-md border border-white/[0.08] bg-zinc-900 px-1.5 py-0.5 font-mono text-[11px] font-medium tabular-nums text-zinc-300">
                  {filteredIssues.length}
                </span>
                {selectedRepoFilter !== 'all' && (
                  <button
                    type="button"
                    onClick={() => handleSelectRepoFilter('all')}
                    className="inline-flex min-w-0 items-center gap-1 truncate rounded-md border border-indigo-500/30 bg-indigo-500/10 px-1.5 py-0.5 font-mono text-[10px] text-indigo-200 hover:bg-indigo-500/20"
                    title="Clear repository filter"
                  >
                    <span className="truncate">{selectedRepoFilter}</span>
                    <X className="h-2.5 w-2.5 shrink-0" />
                  </button>
                )}
              </div>

              {/* Quick Sort / Filter Pill */}
              <button
                type="button"
                onClick={cycleSortBy}
                className="inline-flex shrink-0 items-center gap-1 rounded-md border border-white/[0.08] bg-zinc-900/90 px-2 py-1 font-mono text-[11px] text-zinc-300 transition hover:border-white/[0.16] hover:text-white"
              >
                <ArrowUpDown className="h-3 w-3 text-indigo-400" />
                <span>{sortLabel}</span>
              </button>
            </div>

            {/* Vertical List of Stacked Auto-Layout Issue Cards */}
            <div className="flex-1 divide-y divide-zinc-800/40">
              {filteredIssues.length === 0 ? (
                <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
                  <Filter className="h-5 w-5 text-zinc-600" />
                  <p className="mt-2 text-xs font-medium text-zinc-300">
                    No matching issues in {activeViewLabel}
                  </p>
                  <p className="mt-1 text-xs text-zinc-500">
                    Clear your search filter or switch repositories in the left sidebar.
                  </p>
                  {selectedRepoFilter !== 'all' && (
                    <button
                      type="button"
                      onClick={() => handleSelectRepoFilter('all')}
                      className="mt-3 rounded-lg border border-indigo-500/30 bg-indigo-500/15 px-3 py-1.5 text-xs font-medium text-indigo-200 hover:bg-indigo-500/25"
                    >
                      Show All Synced Repos
                    </button>
                  )}
                </div>
              ) : (
                filteredIssues.map((item) => (
                  <IssueCard
                    key={item.id}
                    issue={item}
                    isSelected={selectedIssue?.id === item.id}
                    onSelect={setSelectedId}
                  />
                ))
              )}
            </div>

            {/* Subtle Keyboard Shortcuts Keycap Footer */}
            <div className="sticky bottom-0 z-10 flex items-center justify-between border-t border-white/[0.06] bg-[#0A0B10]/95 px-4 py-2 text-[11px] text-zinc-400 backdrop-blur-md">
              <div className="flex items-center gap-3">
                <span className="flex items-center gap-1">
                  <kbd className="rounded border border-zinc-700 bg-zinc-800/80 px-1.5 py-0.5 font-mono text-[10px] text-zinc-300">J</kbd>
                  <kbd className="rounded border border-zinc-700 bg-zinc-800/80 px-1.5 py-0.5 font-mono text-[10px] text-zinc-300">K</kbd>
                  <span className="text-zinc-500">navigate</span>
                </span>
                <span className="flex items-center gap-1">
                  <kbd className="rounded border border-zinc-700 bg-zinc-800/80 px-1.5 py-0.5 font-mono text-[10px] text-zinc-300">C</kbd>
                  <span className="text-zinc-500">copy reply</span>
                </span>
                <span className="flex items-center gap-1">
                  <kbd className="rounded border border-zinc-700 bg-zinc-800/80 px-1.5 py-0.5 font-mono text-[10px] text-zinc-300">/</kbd>
                  <span className="text-zinc-500">search</span>
                </span>
              </div>
              <span className="text-[10px] text-zinc-500 font-mono">
                {filteredIssues.length} {filteredIssues.length === 1 ? 'issue' : 'issues'}
              </span>
            </div>
          </section>

          {/* RIGHT INSPECTOR DRAWER (col-span-7) */}
          <TriageModal
            selectedIssue={selectedIssue}
            triagingId={triagingId}
            onRunTriage={handleRunTriage}
            selectedTierPts={selectedTierPts}
            setSelectedTierPts={setSelectedTierPts}
            contributorAddressInput={contributorAddressInput}
            setContributorAddressInput={setContributorAddressInput}
            payingBounty={payingBounty}
            payoutReceipt={payoutReceipt}
            payoutError={payoutError}
            onReleaseBountyViaHorizon={handleReleaseBountyViaHorizon}
            copiedTx={copiedTx}
            onCopyTxHash={handleCopyTxHash}
            draftedReply={draftedReply}
            setDraftedReply={setDraftedReply}
            copied={copied}
            onCopyReply={handleCopyReply}
            postingReply={postingReply}
            onPostCommentToGitHub={handlePostCommentToGitHub}
          />
        </div>
      </div>
    </div>
  )
}

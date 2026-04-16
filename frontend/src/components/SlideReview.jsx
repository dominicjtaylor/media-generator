import React, { useState } from 'react'

function slideLabel(index, total) {
  if (index === 0)         return { label: 'Hook',    color: 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300' }
  if (index === total - 1) return { label: 'CTA',     color: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' }
  return                          { label: 'Content', color: 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400' }
}

function Spinner({ size = 4 }) {
  return (
    <svg
      className={`animate-spin h-${size} w-${size}`}
      xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
    </svg>
  )
}

function RegenIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 4 23 10 17 10"/>
      <polyline points="1 20 1 14 7 14"/>
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
    </svg>
  )
}

function SlideCard({ slide, index, total, flag, onRegenerate, onDismissFlag }) {
  const [regenBusy, setRegenBusy] = useState(false)
  const { label, color } = slideLabel(index, total)

  const handleRegen = async () => {
    if (regenBusy) return
    setRegenBusy(true)
    try {
      await onRegenerate(index, flag || null)
    } finally {
      setRegenBusy(false)
    }
  }

  return (
    <div className={`rounded-xl border bg-white dark:bg-gray-900 p-5 space-y-3 shadow-sm transition-all duration-150 ${
      flag
        ? 'border-amber-300 dark:border-amber-700 ring-1 ring-amber-200 dark:ring-amber-800'
        : 'border-gray-200 dark:border-gray-800'
    }`}>
      {/* Header row */}
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold text-gray-400 dark:text-gray-600 tabular-nums w-4">
          {String(index + 1).padStart(2, '0')}
        </span>
        <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide ${color}`}>
          {label}
        </span>

        <button
          type="button"
          onClick={handleRegen}
          disabled={regenBusy}
          className="ml-auto flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {regenBusy ? <Spinner size={3} /> : <RegenIcon />}
          {regenBusy ? 'Regenerating…' : 'Regenerate'}
        </button>
      </div>

      {/* Slide content */}
      <div>
        <p className="font-semibold text-base leading-snug">{slide.heading}</p>
        {slide.description && (
          <p className="text-sm text-gray-500 dark:text-gray-400 leading-relaxed mt-1.5">{slide.description}</p>
        )}
      </div>

      {/* QC flag */}
      {flag && (
        <div className="flex items-start gap-2 bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-800 rounded-lg px-3 py-2.5">
          <svg className="mt-0.5 shrink-0 text-amber-500" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
            <line x1="12" y1="9" x2="12" y2="13"/>
            <line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-amber-800 dark:text-amber-300">{flag.issue}</p>
            {flag.suggestion && (
              <p className="text-xs text-amber-600 dark:text-amber-400 mt-0.5">{flag.suggestion}</p>
            )}
          </div>
          <button
            type="button"
            onClick={() => onDismissFlag(index)}
            aria-label="Dismiss flag"
            className="shrink-0 text-amber-400 hover:text-amber-600 dark:hover:text-amber-200 transition-colors p-0.5"
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"/>
              <line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
      )}
    </div>
  )
}

export default function SlideReview({
  slides, flags, caption,
  onRegenerate, onDismissFlag, onRerunQc, onRender, onBack, onToast,
}) {
  const [qcBusy,  setQcBusy]  = useState(false)
  const [copied,  setCopied]  = useState(false)

  const flagMap   = Object.fromEntries(flags.map(f => [f.slide_index, f]))
  const flagCount = flags.length

  const handleRerunQc = async () => {
    if (qcBusy) return
    setQcBusy(true)
    try { await onRerunQc() } finally { setQcBusy(false) }
  }

  const handleCopyCaption = async () => {
    try {
      await navigator.clipboard.writeText(caption)
      setCopied(true)
      onToast('Caption copied!')
      setTimeout(() => setCopied(false), 2000)
    } catch {
      onToast('Copy failed', 'error')
    }
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold text-base">Review slides</h2>
          <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            {slides.length} slides
            {flagCount > 0
              ? <span className="ml-2 text-amber-600 dark:text-amber-400">· {flagCount} flag{flagCount > 1 ? 's' : ''}</span>
              : <span className="ml-2 text-emerald-600 dark:text-emerald-400">· all clear</span>
            }
          </p>
        </div>

        <button
          type="button"
          onClick={handleRerunQc}
          disabled={qcBusy}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50 transition-colors border border-gray-200 dark:border-gray-700"
        >
          {qcBusy ? <Spinner size={3} /> : <RegenIcon />}
          {qcBusy ? 'Checking…' : 'Re-run QC'}
        </button>
      </div>

      {/* Slide cards */}
      <div className="space-y-3">
        {slides.map((slide, i) => (
          <SlideCard
            key={i}
            slide={slide}
            index={i}
            total={slides.length}
            flag={flagMap[i] || null}
            onRegenerate={onRegenerate}
            onDismissFlag={onDismissFlag}
          />
        ))}
      </div>

      {/* Caption preview */}
      {caption && (
        <div className="rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 dark:border-gray-800">
            <span className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide">Caption</span>
            <button
              type="button"
              onClick={handleCopyCaption}
              className="text-xs text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
            >
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </div>
          <p className="px-4 py-3 text-xs text-gray-500 dark:text-gray-400 leading-relaxed line-clamp-3">{caption}</p>
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-3">
        <button
          type="button"
          onClick={onBack}
          className="px-4 py-3 rounded-xl text-sm font-medium text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
        >
          Start over
        </button>
        <button
          type="button"
          onClick={onRender}
          className="
            flex-1 flex items-center justify-center gap-2
            bg-accent hover:bg-accent-hover
            text-white font-semibold text-sm
            px-5 py-3 rounded-xl
            transition-all active:scale-[0.98]
            shadow-sm
          "
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polygon points="23 7 16 12 23 17 23 7"/>
            <rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
          </svg>
          Render carousel
        </button>
      </div>
    </div>
  )
}

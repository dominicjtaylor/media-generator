import React, { useMemo } from 'react'

// ---------------------------------------------------------------------------
// CSV parser — handles double-quoted fields with embedded commas
// ---------------------------------------------------------------------------
function parseCSV(text) {
  const lines = text.trim().split(/\r?\n/)
  if (lines.length < 2) return []

  function parseLine(line) {
    const result = []
    let current = ''
    let inQuotes = false
    for (let i = 0; i < line.length; i++) {
      const ch = line[i]
      if (ch === '"') {
        if (inQuotes && line[i + 1] === '"') { current += '"'; i++ }
        else inQuotes = !inQuotes
      } else if (ch === ',' && !inQuotes) {
        result.push(current)
        current = ''
      } else {
        current += ch
      }
    }
    result.push(current)
    return result
  }

  const headers = parseLine(lines[0])
  return lines.slice(1)
    .filter(l => l.trim())
    .map(line => {
      const vals = parseLine(line)
      return Object.fromEntries(headers.map((h, i) => [h, vals[i] ?? '']))
    })
}

// ---------------------------------------------------------------------------
// Download helpers
// ---------------------------------------------------------------------------
function downloadCSV(csvContent, topic) {
  const slug = topic?.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'carousel'
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href     = url
  a.download = `${slug}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

async function copyText(text) {
  await navigator.clipboard.writeText(text)
}

// ---------------------------------------------------------------------------
// Slide type label
// ---------------------------------------------------------------------------
function slideLabel(index, total) {
  if (index === 0)         return { label: 'Hook',    color: 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300' }
  if (index === total - 1) return { label: 'CTA',     color: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' }
  return                          { label: 'Content', color: 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400' }
}

// ---------------------------------------------------------------------------
// Skeleton loader
// ---------------------------------------------------------------------------
function Skeleton() {
  return (
    <div className="space-y-3 animate-fade-in">
      {[100, 85, 90, 80, 95].map((w, i) => (
        <div key={i} className="rounded-xl border border-gray-100 dark:border-gray-800 p-5 space-y-3">
          <div className="skeleton h-3 w-12 rounded-full" />
          <div className={`skeleton h-5 w-[${w}%] rounded-lg`} style={{ width: `${w}%` }} />
          <div className="skeleton h-3.5 w-full rounded" />
          <div className="skeleton h-3.5 w-4/5 rounded" />
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Output component
// ---------------------------------------------------------------------------
export default function Output({ status, csv, errorMsg, onToast }) {
  const slides = useMemo(() => (csv ? parseCSV(csv) : []), [csv])
  const topic  = slides[0]?.Topic ?? ''

  if (status === 'loading') return <Skeleton />

  if (status === 'error') {
    return (
      <div className="rounded-xl border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 p-5 text-sm text-red-600 dark:text-red-400 animate-slide-up">
        <div className="flex items-start gap-3">
          <svg className="mt-0.5 shrink-0" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
          </svg>
          <span>{errorMsg || 'Something went wrong. Please try again.'}</span>
        </div>
      </div>
    )
  }

  if (status !== 'done' || !slides.length) return null

  return (
    <div className="space-y-5 animate-slide-up">

      {/* Section header + download actions */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-semibold text-sm">Your carousel</h2>
          <p className="text-xs text-gray-400 mt-0.5">{slides.length} slides · ready to export</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={async () => {
              await copyText(csv)
              onToast('Copied to clipboard!')
            }}
            className="
              flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium
              text-gray-500 dark:text-gray-400
              hover:text-gray-900 dark:hover:text-gray-100
              hover:bg-gray-100 dark:hover:bg-gray-800
              transition-colors
            "
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
            </svg>
            Copy CSV
          </button>
        </div>
      </div>

      {/* Slide cards */}
      <div className="space-y-3">
        {slides.map((slide, i) => {
          const { label, color } = slideLabel(i, slides.length)
          return (
            <div
              key={i}
              className="
                rounded-xl border border-gray-200 dark:border-gray-800
                bg-white dark:bg-gray-900
                p-5 space-y-2.5 shadow-sm
                hover:shadow-md hover:border-gray-300 dark:hover:border-gray-700
                transition-all duration-150
              "
            >
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-gray-400 dark:text-gray-600 tabular-nums w-4">
                  {String(i + 1).padStart(2, '0')}
                </span>
                <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide ${color}`}>
                  {label}
                </span>
              </div>
              <p className="font-semibold text-base leading-snug">{slide.Heading}</p>
              <p className="text-sm text-gray-500 dark:text-gray-400 leading-relaxed">{slide.Description}</p>
            </div>
          )
        })}
      </div>

      {/* Primary download button */}
      <button
        onClick={() => {
          downloadCSV(csv, topic)
          onToast('CSV downloaded!')
        }}
        className="
          w-full flex items-center justify-center gap-2.5
          bg-accent hover:bg-accent-hover
          text-white font-semibold text-sm
          px-5 py-3.5 rounded-xl
          transition-all active:scale-[0.98]
          shadow-sm hover:shadow-md
        "
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
          <polyline points="7 10 12 15 17 10"/>
          <line x1="12" y1="15" x2="12" y2="3"/>
        </svg>
        Download CSV
      </button>

    </div>
  )
}

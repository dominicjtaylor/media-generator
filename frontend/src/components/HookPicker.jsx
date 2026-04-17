import React, { useState, useRef } from 'react'

const TYPE_LABELS = {
  specific_promise:  'Specific Promise',
  pattern_interrupt: 'Pattern Interrupt',
  contrast:          'Contrast',
  named_thing:       'Named Thing',
}

const TYPE_COLORS = {
  specific_promise:  'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300',
  pattern_interrupt: 'bg-violet-50 text-violet-700 dark:bg-violet-950/40 dark:text-violet-300',
  contrast:          'bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300',
  named_thing:       'bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300',
}

const CUSTOM_COLOR = 'bg-sky-50 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300'

export default function HookPicker({ hooks, topic, onSelect, onBack }) {
  // selected: null | 0–3 (generated) | 'custom'
  const [selected,   setSelected]   = useState(null)
  const [customHook, setCustomHook] = useState('')
  const [busy,       setBusy]       = useState(false)
  const inputRef = useRef(null)

  const isCustomSelected = selected === 'custom'
  const canContinue = !busy && (
    (typeof selected === 'number') ||
    (isCustomSelected && customHook.trim().length >= 3)
  )

  const handleContinue = () => {
    if (!canContinue) return
    setBusy(true)
    onSelect(isCustomSelected
      ? { type: 'custom', hook: customHook.trim() }
      : hooks[selected]
    )
  }

  const handleCustomCardClick = () => {
    setSelected('custom')
    inputRef.current?.focus()
  }

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h2 className="font-semibold text-base">Pick your hook</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-0.5">
          Choose the opening that best fits your style for{' '}
          <span className="font-medium text-gray-700 dark:text-gray-300">"{topic}"</span>
        </p>
      </div>

      <div className="space-y-3">
        {/* Generated hook cards */}
        {hooks.map((h, i) => {
          const colorClass = TYPE_COLORS[h.type] || 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'
          const label      = TYPE_LABELS[h.type] || h.type
          const isSelected = selected === i
          return (
            <button
              key={i}
              type="button"
              onClick={() => setSelected(i)}
              className={`
                w-full text-left p-4 rounded-xl border transition-all duration-150
                ${isSelected
                  ? 'border-accent bg-accent/5 dark:bg-accent/10 shadow-sm ring-2 ring-accent/20'
                  : 'border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 hover:border-gray-300 dark:hover:border-gray-700 hover:shadow-sm'
                }
              `}
            >
              <div className="flex items-center gap-2 mb-2">
                <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide ${colorClass}`}>
                  {label}
                </span>
                {isSelected && (
                  <svg
                    className="ml-auto text-accent" width="14" height="14"
                    viewBox="0 0 24 24" fill="none" stroke="currentColor"
                    strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
                  >
                    <polyline points="20 6 9 17 4 12"/>
                  </svg>
                )}
              </div>
              <p className="text-sm font-medium leading-snug text-gray-900 dark:text-gray-100">{h.hook}</p>
            </button>
          )
        })}

        {/* Custom hook card */}
        <div
          onClick={handleCustomCardClick}
          className={`
            p-4 rounded-xl border transition-all duration-150 cursor-text
            ${isCustomSelected
              ? 'border-accent bg-accent/5 dark:bg-accent/10 shadow-sm ring-2 ring-accent/20'
              : 'border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 hover:border-gray-300 dark:hover:border-gray-700 hover:shadow-sm'
            }
          `}
        >
          <div className="flex items-center gap-2 mb-2">
            <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide ${CUSTOM_COLOR}`}>
              Write your own
            </span>
            {isCustomSelected && customHook.trim().length >= 3 && (
              <svg
                className="ml-auto text-accent" width="14" height="14"
                viewBox="0 0 24 24" fill="none" stroke="currentColor"
                strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
              >
                <polyline points="20 6 9 17 4 12"/>
              </svg>
            )}
          </div>
          <input
            ref={inputRef}
            type="text"
            value={customHook}
            onChange={e => setCustomHook(e.target.value)}
            onClick={e => e.stopPropagation()}
            placeholder="e.g. The mistake that cost me 6 months…"
            className="
              w-full bg-transparent text-sm font-medium
              text-gray-900 dark:text-gray-100
              placeholder-gray-400 dark:placeholder-gray-600
              outline-none
            "
          />
        </div>
      </div>

      <div className="flex gap-3">
        <button
          type="button"
          onClick={onBack}
          className="px-4 py-3 rounded-xl text-sm font-medium text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
        >
          Back
        </button>
        <button
          type="button"
          onClick={handleContinue}
          disabled={!canContinue}
          className="
            flex-1 flex items-center justify-center gap-2
            bg-accent hover:bg-accent-hover
            disabled:opacity-40 disabled:cursor-not-allowed
            text-white font-semibold text-sm
            px-5 py-3 rounded-xl
            transition-all active:scale-[0.98]
            shadow-sm
          "
        >
          {busy ? (
            <>
              <svg className="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
              </svg>
              Generating slides…
            </>
          ) : (
            <>
              Use this hook
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="9 18 15 12 9 6"/>
              </svg>
            </>
          )}
        </button>
      </div>
    </div>
  )
}

import React, { useState, useRef, useEffect } from 'react'

const SLIDES = [4, 5, 7, 10]

export default function Form({ onGenerate, loading, onReset }) {
  const [topic,    setTopic]    = useState('')
  const [slides,   setSlides]   = useState(5)
  const [expanded, setExpanded] = useState(false)
  const inputRef = useRef(null)

  useEffect(() => { inputRef.current?.focus() }, [])

  const canSubmit = topic.trim().length > 0 && !loading

  const handleSubmit = (e) => {
    e?.preventDefault()
    if (!canSubmit) return
    onGenerate({ topic, slides })
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">

      {/* Topic input */}
      <div className="relative">
        <textarea
          ref={inputRef}
          rows={3}
          value={topic}
          onChange={e => setTopic(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="e.g. The benefits of daily journaling"
          disabled={loading}
          className="
            w-full resize-none rounded-xl px-4 py-3.5 text-sm leading-relaxed
            bg-white dark:bg-gray-900
            border border-gray-200 dark:border-gray-800
            placeholder-gray-400 dark:placeholder-gray-600
            focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent
            disabled:opacity-50 disabled:cursor-not-allowed
            transition-shadow shadow-sm
          "
        />
        <span className="absolute bottom-3 right-3 text-xs text-gray-300 dark:text-gray-700 select-none">
          ↵ enter
        </span>
      </div>

      {/* Advanced settings — collapsible */}
      <div className="rounded-xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 overflow-hidden shadow-sm">
        <button
          type="button"
          onClick={() => setExpanded(e => !e)}
          className="
            w-full flex items-center justify-between px-4 py-3 text-sm
            text-gray-500 dark:text-gray-400
            hover:text-gray-900 dark:hover:text-gray-100
            hover:bg-gray-50 dark:hover:bg-gray-800/50
            transition-colors select-none
          "
        >
          <span className="flex items-center gap-2 font-medium">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3"/>
              <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
              <path d="M4.93 4.93a10 10 0 0 0 0 14.14"/>
            </svg>
            Settings
          </span>
          <svg
            width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
            style={{ transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }}
          >
            <polyline points="6 9 12 15 18 9"/>
          </svg>
        </button>

        {expanded && (
          <div className="px-4 pb-4 pt-1 border-t border-gray-100 dark:border-gray-800 animate-fade-in">
            <div className="space-y-1.5">
              <label className="block text-xs font-medium text-gray-500 dark:text-gray-400">Slides</label>
              <div className="flex gap-2">
                {SLIDES.map(n => (
                  <button
                    key={n}
                    type="button"
                    onClick={() => setSlides(n)}
                    className={`
                      text-sm px-3 py-1.5 rounded-lg transition-colors
                      ${slides === n
                        ? 'bg-accent text-white font-medium'
                        : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
                      }
                    `}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex gap-3">
        <button
          type="submit"
          disabled={!canSubmit}
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
          {loading ? (
            <>
              <Spinner />
              Generating…
            </>
          ) : (
            <>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
              </svg>
              Generate carousel
            </>
          )}
        </button>

        {onReset && !loading && (
          <button
            type="button"
            onClick={onReset}
            className="
              px-4 py-3 rounded-xl text-sm font-medium
              text-gray-500 dark:text-gray-400
              hover:text-gray-900 dark:hover:text-gray-100
              hover:bg-gray-100 dark:hover:bg-gray-800
              transition-colors
            "
          >
            Reset
          </button>
        )}
      </div>
    </form>
  )
}

function Spinner() {
  return (
    <svg className="animate-spin" width="15" height="15" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeOpacity=".25"/>
      <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round"/>
    </svg>
  )
}

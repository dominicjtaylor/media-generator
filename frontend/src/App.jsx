import React, { useState, useEffect, useCallback, useRef } from 'react'
import Form from './components/Form.jsx'
import HookPicker from './components/HookPicker.jsx'
import ImagePicker from './components/ImagePicker.jsx'
import SlideReview from './components/SlideReview.jsx'
import Output from './components/Output.jsx'
import Toast from './components/Toast.jsx'

// Stage flow:
// idle → template_selection → hooks_loading → hook_selection
//   Dark:  → image_selection → slides_loading → review → rendering → done
//   Light: → light_upload → light_generating → done
// Any stage → error

const ensureSlides = (s) => Array.isArray(s) ? s : []

function LoadingPane({ message }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-4 animate-fade-in">
      <svg className="animate-spin h-6 w-6 text-accent" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
      </svg>
      <p className="text-sm text-gray-500 dark:text-gray-400">{message || 'Working…'}</p>
    </div>
  )
}

async function readSse(res, onEvent) {
  const reader  = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer    = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop()
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      try { onEvent(JSON.parse(line.slice(6))) } catch { /* ignore malformed */ }
    }
  }
}

// ── Shared: two-card selection screen ─────────────────────────────────────
function CardSelector({ title, description, options, onSelect, onBack }) {
  return (
    <div className="space-y-6 animate-slide-up">
      <div>
        <h2 className="text-xl font-semibold">{title}</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">{description}</p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {options.map(opt => (
          <button
            key={opt.value}
            onClick={() => onSelect(opt.value)}
            className="rounded-xl border border-gray-200 dark:border-gray-800 p-5 text-left hover:border-accent hover:bg-gray-50 dark:hover:bg-gray-900/60 transition-all group"
          >
            {opt.preview}
            <div className="font-semibold text-sm group-hover:text-accent transition-colors">{opt.label}</div>
            <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{opt.description}</div>
          </button>
        ))}
      </div>

      <button
        onClick={onBack}
        className="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
      >
        ← Back
      </button>
    </div>
  )
}

const TEMPLATE_OPTIONS = [
  {
    value: 'dark',
    preview: (
      <div className="w-full h-28 rounded-lg bg-gradient-to-br from-gray-900 to-gray-950 mb-4 flex items-center justify-center border border-gray-700">
        <span className="text-white text-4xl font-black tracking-tight" style={{ fontFamily: 'serif' }}>Aa</span>
      </div>
    ),
    label: 'Dark Template',
    description: 'Bold headings on dark background with library image',
  },
  {
    value: 'light',
    preview: (
      <div className="w-full h-28 rounded-lg bg-gradient-to-br from-gray-50 to-gray-100 dark:from-gray-200 dark:to-gray-300 mb-4 flex items-center justify-center border border-gray-200">
        <span className="text-gray-900 text-4xl font-black tracking-tight" style={{ fontFamily: 'serif' }}>Aa</span>
      </div>
    ),
    label: 'Light Template',
    description: 'Image-driven slides — you upload one photo per slide',
  },
]

const CONTENT_MODE_OPTIONS = [
  {
    value: 'llm',
    preview: (
      <div className="w-full h-20 rounded-lg bg-gradient-to-br from-violet-950 to-violet-900 mb-4 flex items-center justify-center border border-violet-800">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
        </svg>
      </div>
    ),
    label: 'AI Generated',
    description: 'Claude writes every slide from your topic',
  },
  {
    value: 'manual',
    preview: (
      <div className="w-full h-20 rounded-lg bg-gradient-to-br from-gray-800 to-gray-900 mb-4 flex items-center justify-center border border-gray-700">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
          <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
        </svg>
      </div>
    ),
    label: 'Manual Input',
    description: 'You write every slide heading and body',
  },
]

function TemplateSelector({ onSelect, onBack }) {
  return (
    <CardSelector
      title="Choose a template"
      description="Select the visual style for your carousel."
      options={TEMPLATE_OPTIONS}
      onSelect={onSelect}
      onBack={onBack}
    />
  )
}

function ContentModeSelector({ onSelect, onBack }) {
  return (
    <CardSelector
      title="How should content be created?"
      description="Choose how the slide text will be written."
      options={CONTENT_MODE_OPTIONS}
      onSelect={onSelect}
      onBack={onBack}
    />
  )
}

// ── Shared: manual slide content entry (dark with hook, light without) ────
function ContentEntryForm({ numSlides, includeHook = false, onConfirm, onBack }) {
  const contentCount = numSlides - 2
  const [hookText, setHookText] = useState('')
  const [entries, setEntries] = useState(
    Array.from({ length: contentCount }, () => ({ heading: '', text: '' }))
  )

  const update = (i, field, value) =>
    setEntries(prev => prev.map((e, idx) => idx === i ? { ...e, [field]: value } : e))

  const allFilled = (!includeHook || hookText.trim()) && entries.every(e => e.heading.trim() && e.text.trim())

  const handleConfirm = () => {
    if (!allFilled) return
    onConfirm(includeHook ? { hook: hookText, slidesContent: entries } : entries)
  }

  return (
    <div className="space-y-6 animate-slide-up">
      <div>
        <h2 className="text-xl font-semibold">Enter your slide content</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          {includeHook
            ? 'Write the hook and body for each slide. The final CTA slide is added automatically.'
            : `Add a heading and body text for each content slide (slides 2–${numSlides - 1}).`}
        </p>
      </div>

      <div className="space-y-5">
        {includeHook && (
          <div className="rounded-xl border border-violet-200 dark:border-violet-900/50 p-4 space-y-3">
            <p className="text-xs font-semibold text-violet-500 uppercase tracking-wide">Slide 1 — Hook</p>
            <input
              type="text"
              placeholder="Hook heading (required)"
              value={hookText}
              onChange={e => setHookText(e.target.value)}
              className="w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent/40"
            />
          </div>
        )}

        {entries.map((entry, i) => (
          <div key={i} className="rounded-xl border border-gray-200 dark:border-gray-800 p-4 space-y-3">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Slide {i + 2}</p>
            <input
              type="text"
              placeholder="Heading (required)"
              value={entry.heading}
              onChange={e => update(i, 'heading', e.target.value)}
              className="w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent/40"
            />
            <textarea
              rows={2}
              placeholder="Body text (required)"
              value={entry.text}
              onChange={e => update(i, 'text', e.target.value)}
              className="w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent/40 resize-none"
            />
          </div>
        ))}
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
          onClick={handleConfirm}
          disabled={!allFilled}
          className="flex-1 rounded-xl bg-accent text-white py-3 text-sm font-semibold disabled:opacity-40 disabled:cursor-not-allowed hover:bg-orange-600 transition-colors"
        >
          Continue
        </button>
      </div>
    </div>
  )
}

// ── Light image upload ─────────────────────────────────────────────────────
function LightUpload({ onConfirm, onBack, selectedImage}) {
  const [files, setFiles] = useState([])
  const [previews, setPreviews] = useState([])

  const handleChange = (e) => {
    const selected = Array.from(e.target.files).slice(0, 8)
    setFiles(selected)
    // Revoke old previews
    previews.forEach(URL.revokeObjectURL)
    setPreviews(selected.map(f => URL.createObjectURL(f)))
  }

  useEffect(() => {
    return () => previews.forEach(URL.revokeObjectURL)
  }, [previews])

  const canGenerate = files.length >= 2 && selectedImage

  return (
    <div className="space-y-6 animate-slide-up">
      <div>
        <h2 className="text-xl font-semibold">Upload your images</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Step 2: Upload 2–8 images for the content slides.
        </p>
      </div>

      {selectedImage && (
        <div className="flex items-center gap-3 p-3 border rounded-lg">
          <img src={selectedImage.thumbnail_url} className="w-12 h-12 rounded object-cover" />
          <span className="text-sm">Cover image selected</span>
        </div>
      )}

      <label className="block cursor-pointer rounded-xl border-2 border-dashed border-gray-300 dark:border-gray-700 hover:border-accent transition-colors p-8 text-center">
        <input
          type="file"
          className="sr-only"
          accept="image/*"
          multiple
          onChange={handleChange}
        />
        <div className="flex flex-col items-center gap-2 text-gray-400 dark:text-gray-500">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/>
            <polyline points="21 15 16 10 5 21"/>
          </svg>
          <span className="text-sm">
            {files.length > 0
              ? `${files.length} image${files.length > 1 ? 's' : ''} selected — click to change`
              : 'Click to select images (2–8)'}
          </span>
        </div>
      </label>

      {previews.length > 0 && (
        <div className="grid grid-cols-4 gap-2">
          {previews.map((src, i) => (
            <div key={i} className="aspect-square rounded-lg overflow-hidden bg-gray-100 dark:bg-gray-800">
              <img src={src} alt={`Preview ${i + 1}`} className="w-full h-full object-cover" />
            </div>
          ))}
        </div>
      )}

      {files.length > 0 && files.length < 2 && (
        <p className="text-xs text-amber-600 dark:text-amber-400">Please select at least 2 images.</p>
      )}

      <div className="flex gap-3">
        <button
          onClick={onBack}
          className="flex-1 rounded-xl border border-gray-200 dark:border-gray-800 py-3 text-sm font-medium hover:bg-gray-50 dark:hover:bg-gray-900 transition-colors"
        >
          Back
        </button>
        <button
          onClick={() => onConfirm(files)}
          disabled={!canGenerate}
          className="flex-[2] rounded-xl bg-accent text-white py-3 text-sm font-semibold disabled:opacity-40 disabled:cursor-not-allowed hover:bg-orange-600 transition-colors"
        >
          {canGenerate
            ? `Generate carousel (${files.length} slide${files.length > 1 ? 's' : ''})`
            : 'Select at least 2 images'}
        </button>
      </div>
    </div>
  )
}

// ── Light: hook picker (3 options) ────────────────────────────────────────
const HOOK_TYPE_LABELS = {
  specific_promise: 'Specific Promise',
  pattern_interrupt: 'Pattern Interrupt',
  contrast: 'Contrast',
}

function LightHookPicker({ hooks, onSelect, onBack }) {
  const [selected, setSelected] = useState(null)

  return (
    <div className="space-y-6 animate-slide-up">
      <div>
        <h2 className="text-xl font-semibold">Choose your hook</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          This becomes the heading on slide 1.
        </p>
      </div>

      <div className="space-y-3">
        {hooks.map((h, i) => (
          <button
            key={i}
            type="button"
            onClick={() => setSelected(h)}
            className={`
              w-full text-left rounded-xl border-2 p-4 transition-all
              ${selected?.hook === h.hook
                ? 'border-accent bg-accent/5'
                : 'border-gray-200 dark:border-gray-800 hover:border-gray-300 dark:hover:border-gray-700'
              }
            `}
          >
            <span className="text-xs font-semibold uppercase tracking-wide text-accent mb-1 block">
              {HOOK_TYPE_LABELS[h.type] || h.type}
            </span>
            <span className="text-sm font-medium leading-snug">{h.hook}</span>
          </button>
        ))}
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
          onClick={() => selected && onSelect(selected)}
          disabled={!selected}
          className="flex-1 rounded-xl bg-accent text-white py-3 text-sm font-semibold disabled:opacity-40 disabled:cursor-not-allowed hover:bg-orange-600 transition-colors"
        >
          Use this hook
        </button>
      </div>
    </div>
  )
}

const LightContentEntry = (props) => <ContentEntryForm {...props} includeHook={false} />

// ── Main App ───────────────────────────────────────────────────────────────
export default function App() {
  const [dark, setDark]       = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches)
  const [status, setStatus]   = useState('idle')
  const [stepMsg, setStepMsg] = useState('')
  const [errorMsg, setError]  = useState('')
  const [toast, setToast]     = useState(null)

  // Pipeline data
  const [topic,             setTopic]             = useState('')
  const [numSlides,         setNumSlides]         = useState(5)
  const [templateType,      setTemplateType]      = useState(null)   // 'dark' | 'light'
  const [contentMode,       setContentMode]       = useState(null)   // 'llm' | 'manual'
  const [hooks,             setHooks]             = useState([])
  const [lightHooks,        setLightHooks]        = useState([])
  const [lightSlideContent, setLightSlideContent] = useState([])  // [{heading, text}] for content slides
  const [manualHook,        setManualHook]        = useState('')
  const [manualSlides,      setManualSlides]      = useState([])   // [{heading, text}] for manual dark mode
  const [selectedHook,      setSelectedHook]      = useState(null)
  const [selectedImage,     setSelectedImage]     = useState(null)
  const [slides,            setSlides]            = useState([])
  const [caption,           setCaption]           = useState('')
  const [carouselStyle,     setCarouselStyle]     = useState('dark_core')
  const [images,            setImages]            = useState([])

  const slidesRef = useRef(slides)
  useEffect(() => { slidesRef.current = slides }, [slides])

  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
  }, [dark])

  const showToast = useCallback((message, type = 'success') => {
    setToast({ message, type })
    setTimeout(() => setToast(null), 3500)
  }, [])

  const goError = useCallback((msg) => {
    setError(msg)
    setStatus('error')
    showToast(msg, 'error')
  }, [showToast])

  const reset = useCallback(() => {
    setStatus('idle')
    setError('')
    setStepMsg('')
    setTemplateType(null)
    setContentMode(null)
    setHooks([])
    setLightHooks([])
    setLightSlideContent([])
    setManualHook('')
    setManualSlides([])
    setSelectedHook(null)
    setSelectedImage(null)
    setSlides([])
    setCaption('')
    setImages([])
  }, [])

  // ── Stage 1: form submit → template selection ──────────────────────────
  const handleFormSubmit = useCallback(({ topic: t, slides: n }) => {
    setTopic(t.trim())
    setNumSlides(n)
    setStatus('template_selection')
  }, [])

  // ── Stage 2: template selected → content mode (dark) or hooks (light) ───
  const handleTemplateSelect = useCallback(async (type) => {
    setTemplateType(type)

    if (type === 'light') {
      // Light: call /light-hooks for 3 hook ideas
      setStatus('light_hooks_loading')
      setStepMsg('Generating hook options…')
      try {
        const res = await fetch('/light-hooks', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ topic, num_slides: numSlides }),
        })
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          throw new Error(body.detail || `Server error (${res.status})`)
        }
        const data = await res.json()
        setLightHooks(data.hooks || [])
        setStatus('light_hook_selection')
      } catch (err) {
        goError(err.message || 'Failed to generate hooks.')
      }
    } else {
      // Dark: choose content mode before generating anything
      setStatus('dark_content_mode')
    }
  }, [topic, numSlides, goError])

  // ── Stage 2b: content mode selected ───────────────────────────────────
  const handleContentModeSelect = useCallback(async (mode) => {
    setContentMode(mode)

    if (mode === 'llm') {
      // LLM path: generate hook options via Claude
      setStatus('hooks_loading')
      setStepMsg('Generating hook options…')
      try {
        const res = await fetch('/hooks', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ topic, num_slides: numSlides }),
        })
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          throw new Error(body.detail || `Server error (${res.status})`)
        }
        const data = await res.json()
        setHooks(data.hooks || [])
        setStatus('hook_selection')
      } catch (err) {
        goError(err.message || 'Failed to generate hooks.')
      }
    } else {
      // Manual path: user writes content directly
      setStatus('dark_manual_entry')
    }
  }, [topic, numSlides, goError])

  // ── Stage 3a: dark hook selected → image selection ───────────────────
  const handleHookSelect = useCallback((hook) => {
    setSelectedHook(hook)
    setStatus('image_selection')
  }, [])

  // ── Stage 3a (manual): manual content confirmed → image selection ─────
  const handleManualConfirm = useCallback(({ hook, slidesContent }) => {
    setManualHook(hook)
    setManualSlides(slidesContent)
    setStatus('image_selection')
  }, [])

  // ── Stage 3b: light hook selected → content entry ─────────────────────
  const handleLightHookSelect = useCallback((hook) => {
    setSelectedHook(hook)
    setStatus('light_content_entry')
  }, [])

  // ── Stage 3c: light content confirmed → cover image selection ─────────
  const handleLightContentConfirm = useCallback((contentSlides) => {
    setLightSlideContent(contentSlides)
    setStatus('light_cover_selection')
  }, [])

  // ── Dark manual pipeline: render authored slides directly ─────────────
  const handleManualRender = useCallback(async (image) => {
    setStatus('rendering')
    setStepMsg('Rendering slides…')
    try {
      const res = await fetch('/render-manual', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          topic,
          hook:           manualHook,
          slides_content: manualSlides,
          image_filename: image?.filename || null,
          style:          'dark_core',
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Server error (${res.status})`)
      }
      let gotComplete = false
      let gotError = false
      await readSse(res, (event) => {
        if (event.step === 'complete') {
          gotComplete = true
          setImages(event.images || [])
          setCaption(event.caption || '')
          setStatus('done')
          showToast('Carousel rendered!')
        } else if (event.step === 'error') {
          gotError = true
          goError(event.message || 'Rendering failed.')
        } else {
          setStepMsg(event.message || '')
        }
      })
      if (!gotComplete && !gotError) goError('Render did not complete.')
    } catch (err) {
      goError(err.message || 'Rendering failed.')
    }
  }, [topic, manualHook, manualSlides, goError, showToast])

  // ── Dark pipeline: image confirmed → generate (LLM) or render (manual) ─
  const handleImageConfirm = useCallback(async (image) => {
    setSelectedImage(image)
    if (contentMode === 'manual') {
      await handleManualRender(image)
      return
    }
    setStatus('slides_loading')
    setStepMsg('Generating slides…')
    try {
      const res = await fetch('/slides', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          topic,
          hook:           selectedHook.hook,
          num_slides:     numSlides,
          image_filename: image?.filename || null,
          template: templateType,
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Server error (${res.status})`)
      }
      let gotComplete = false
      let gotError = false
      await readSse(res, (event) => {
        if (event.step === 'complete') {
          gotComplete = true
          setSlides(ensureSlides(event.slides))
          setCaption(event.caption || '')
          setCarouselStyle(event.style || 'dark_core')
          setStatus('review')
        } else if (event.step === 'error') {
          gotError = true
          goError(event.message || 'Slide generation failed.')
        } else {
          setStepMsg(event.message || '')
        }
      })
      if (!gotComplete && !gotError) goError('Slide generation did not complete.')
    } catch (err) {
      goError(err.message || 'Slide generation failed.')
    }
  }, [contentMode, topic, numSlides, selectedHook, templateType, handleManualRender, goError])

  // ── Dark pipeline: per-slide regenerate ───────────────────────────────
  const handleRegenerate = useCallback(async (index) => {
    try {
      const res = await fetch('/regenerate', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          topic,
          slide_index:    index,
          hook:           selectedHook?.hook || '',
          slides:         slidesRef.current,
          suggestion:     '',
          template_style: carouselStyle,
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Regeneration failed (${res.status})`)
      }
      const data = await res.json()
      const regenerated = data.slide
      setSlides(prev => ensureSlides(prev).map((s, i) =>
        i === index ? regenerated : s
      ))
      showToast('Slide regenerated!')
    } catch (err) {
      showToast(err.message || 'Regeneration failed.', 'error')
    }
  }, [topic, selectedHook, carouselStyle, showToast])

  // ── Dark pipeline: render ──────────────────────────────────────────────
  const handleRender = useCallback(async (modifiedSlides = null) => {
    setStatus('rendering')
    setStepMsg('Rendering slides…')
    const slidesToRender = modifiedSlides || slidesRef.current
    try {
      const res = await fetch('/render', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ topic, slides: slidesToRender, style: carouselStyle, image_filename: selectedImage?.filename || null }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Render failed (${res.status})`)
      }
      let gotComplete = false
      let gotError = false
      await readSse(res, (event) => {
        if (event.step === 'complete') {
          gotComplete = true
          setImages(event.images || [])
          setStatus('done')
          showToast('Carousel rendered!')
        } else if (event.step === 'error') {
          gotError = true
          goError(event.message || 'Rendering failed.')
        } else {
          setStepMsg(event.message || '')
        }
      })
      if (!gotComplete && !gotError) goError('Render did not complete.')
    } catch (err) {
      goError(err.message || 'Rendering failed.')
    }
  }, [topic, carouselStyle, selectedImage, goError, showToast])

  // ── Light pipeline: images uploaded → generate-light (SSE) ───────────
  const handleLightGenerate = useCallback(async (imageFiles) => {
    if (!selectedImage) {
      showToast('Please select a cover image for slide 1', 'error')
      return
    }
    setStatus('light_generating')
    setStepMsg('Building slides…')

    const formData = new FormData()
    formData.append('topic', topic)
    formData.append('hook', selectedHook.hook)
    formData.append('slides_content', JSON.stringify(lightSlideContent))
    if (selectedImage?.filename) {
      formData.append('image_filename', selectedImage.filename)
    }
    for (const file of imageFiles) {
      formData.append('images', file)
    }

    try {
      const res = await fetch('/generate-light', { method: 'POST', body: formData })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Server error (${res.status})`)
      }
      let gotComplete = false
      let gotError = false
      await readSse(res, (event) => {

        if (event.step === 'complete') {
          setSlides(ensureSlides(event.slides))
          setImages(event.images || [])
          setCaption(event.caption || '')
          setStatus('done')
          showToast('Carousel generated!')

          gotComplete = true

        } else if (event.step === 'error') {
          gotError = true
          goError(event.message || 'Generation failed.')
        } else {
          setStepMsg(event.message || '')
        }
      })
      if (!gotComplete && !gotError) goError('Generation did not complete.')
    } catch (err) {
      goError(err.message || 'Generation failed.')
    }
  }, [topic, selectedHook, selectedImage, lightSlideContent, goError, showToast])

  const LOADING_STAGES = new Set(['hooks_loading', 'light_hooks_loading', 'slides_loading', 'rendering', 'light_generating'])

  return (
    <div className="min-h-screen flex flex-col">

      {/* ── Header ─────────────────────────────────────────────── */}
      <header className="border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-950">
        <div className="max-w-2xl mx-auto px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="w-6 h-6 rounded-md bg-accent flex items-center justify-center">
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <rect x="1" y="1" width="5" height="5" rx="1" fill="white"/>
                <rect x="8" y="1" width="5" height="5" rx="1" fill="white" fillOpacity=".6"/>
                <rect x="1" y="8" width="5" height="5" rx="1" fill="white" fillOpacity=".6"/>
                <rect x="8" y="8" width="5" height="5" rx="1" fill="white" fillOpacity=".3"/>
              </svg>
            </span>
            <span className="font-semibold text-sm tracking-tight">Carousel</span>
          </div>

          <button
            onClick={() => setDark(d => !d)}
            aria-label="Toggle dark mode"
            className="w-8 h-8 rounded-lg flex items-center justify-center text-gray-500 hover:text-gray-900 hover:bg-gray-100 dark:hover:text-gray-100 dark:hover:bg-gray-800 transition-colors"
          >
            {dark ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/>
                <line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>
                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/>
                <line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>
                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
              </svg>
            )}
          </button>
        </div>
      </header>

      {/* ── Main ───────────────────────────────────────────────── */}
      <main className="flex-1 max-w-2xl mx-auto w-full px-4 py-12 flex flex-col gap-10">

        {/* Hero */}
        <div className="text-center space-y-2">
          <h1 className="text-3xl font-bold tracking-tight">Generate your carousel</h1>
          <p className="text-gray-500 dark:text-gray-400 text-sm">
            Turn any topic into a polished Instagram carousel in seconds.
          </p>
        </div>

        {/* ── idle: show form ── */}
        {status === 'idle' && (
          <Form onGenerate={handleFormSubmit} loading={false} onReset={null} />
        )}

        {/* ── template selection ── */}
        {status === 'template_selection' && (
          <TemplateSelector
            onSelect={handleTemplateSelect}
            onBack={() => setStatus('idle')}
          />
        )}

        {/* ── loading spinner ── */}
        {LOADING_STAGES.has(status) && <LoadingPane message={stepMsg} />}

        {/* ── dark: content mode selection ── */}
        {status === 'dark_content_mode' && (
          <ContentModeSelector
            onSelect={handleContentModeSelect}
            onBack={() => setStatus('template_selection')}
          />
        )}

        {/* ── dark: manual slide content entry ── */}
        {status === 'dark_manual_entry' && (
          <ContentEntryForm
            numSlides={numSlides}
            includeHook
            onConfirm={handleManualConfirm}
            onBack={() => setStatus('dark_content_mode')}
          />
        )}

        {/* ── dark: hook selection ── */}
        {status === 'hook_selection' && (
          <HookPicker
            hooks={hooks}
            topic={topic}
            onSelect={handleHookSelect}
            onBack={() => setStatus('dark_content_mode')}
          />
        )}

        {/* ── light: hook selection (3 hooks) ── */}
        {status === 'light_hook_selection' && (
          <LightHookPicker
            hooks={lightHooks}
            onSelect={handleLightHookSelect}
            onBack={() => setStatus('template_selection')}
          />
        )}

        {/* ── light: manual slide content entry ── */}
        {status === 'light_content_entry' && (
          <LightContentEntry
            numSlides={numSlides}
            onConfirm={handleLightContentConfirm}
            onBack={() => setStatus('light_hook_selection')}
          />
        )}

        {/* ── dark: image selection (used by both LLM and manual modes) ── */}
        {status === 'image_selection' && (
          <ImagePicker
            onSelect={handleImageConfirm}
            onBack={() => setStatus(contentMode === 'manual' ? 'dark_manual_entry' : 'hook_selection')}
          />
        )}

        {/* ── light: cover image selection ── */}
        {status === 'light_cover_selection' && (
          <ImagePicker
            onSelect={(img) => {
              setSelectedImage(img)
              setStatus('light_upload')
            }}
            onBack={() => setStatus('light_content_entry')}
          />
        )}

        {/* ── light: image upload ── */}
        {status === 'light_upload' && (
          <LightUpload
            onConfirm={handleLightGenerate}
            onBack={() => setStatus('light_cover_selection')}
            selectedImage={selectedImage}
          />
        )}

        {/* ── dark: slide review ── */}
        {status === 'review' && slides?.length > 0 && (
          <SlideReview
            slides={slides}
            caption={caption}
            onRegenerate={handleRegenerate}
            onRender={handleRender}
            onBack={reset}
            onToast={showToast}
          />
        )}

        {/* ── error ── */}
        {status === 'error' && (
          <div className="rounded-xl border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950/30 p-5 text-sm text-red-600 dark:text-red-400 animate-slide-up">
            <div className="flex items-start gap-3">
              <svg className="mt-0.5 shrink-0" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
              </svg>
              <div className="space-y-2 flex-1">
                <span>{errorMsg}</span>
                <button onClick={reset} className="block text-xs underline opacity-70 hover:opacity-100">
                  Start over
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ── done: images + caption ── */}
        {status === 'done' && (
          <>
            <Output
              status="done"
              data={{ images, slides, caption }}
              errorMsg=""
              stepMessage=""
              onToast={showToast}
            />
            <button
              onClick={reset}
              className="mx-auto block text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
            >
              Start over
            </button>
          </>
        )}
      </main>

      {/* ── Footer ─────────────────────────────────────────────── */}
      <footer className="text-center text-xs text-gray-400 dark:text-gray-600 py-6">
        Powered by Claude
      </footer>

      {toast && <Toast message={toast.message} type={toast.type} />}
    </div>
  )
}

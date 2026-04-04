import { useState, useRef, useEffect } from 'react'

function App() {
  const [file, setFile] = useState(null)
  const [initials, setInitials] = useState('JT')
  const [hourlyRate, setHourlyRate] = useState('516')
  const [headless, setHeadless] = useState(true)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [success, setSuccess] = useState(false)
  const [zipBlob, setZipBlob] = useState(null)

  // Progress state
  const [progressMessages, setProgressMessages] = useState([])
  const [currentStep, setCurrentStep] = useState(0)
  const [totalSteps, setTotalSteps] = useState(0)
  const progressEndRef = useRef(null)

  // win32print state
  const [win32Available, setWin32Available] = useState(null)   // null = unknown
  const [win32Installing, setWin32Installing] = useState(false)
  const [win32Message, setWin32Message] = useState(null)

  // Check win32print status on mount
  useEffect(() => {
    fetch('/api/win32print-status')
      .then(r => r.json())
      .then(d => setWin32Available(d.available))
      .catch(() => setWin32Available(false))
  }, [])

  const scrollToBottom = () => {
    progressEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  const handleInstallWin32 = async () => {
    setWin32Installing(true)
    setWin32Message(null)
    try {
      const res = await fetch('/api/install-win32print', { method: 'POST' })
      const data = await res.json()
      if (res.ok && data.success) {
        setWin32Available(data.available)
        setWin32Message({ type: 'success', text: data.message })
      } else {
        setWin32Message({ type: 'error', text: data.error || 'Installation failed.' })
      }
    } catch (err) {
      setWin32Message({ type: 'error', text: 'Could not reach server: ' + err.message })
    } finally {
      setWin32Installing(false)
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!file) {
      setError('Please select an Excel file.')
      return
    }

    setError(null)
    setLoading(true)
    setSuccess(false)
    setProgressMessages([])
    setCurrentStep(0)
    setTotalSteps(0)

    const sessionId = 'session_' + Date.now() + '_' + Math.random().toString(36).substr(2, 6)

    // Start SSE BEFORE the POST
    const evtSource = new EventSource(`/api/progress/${sessionId}`)
    evtSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        setProgressMessages((prev) => [...prev, data.message])
        if (data.step !== undefined && data.step !== null) setCurrentStep(data.step)
        if (data.total !== undefined && data.total !== null) setTotalSteps(data.total)
        setTimeout(scrollToBottom, 50)
        if (data.status === 'done' || data.status === 'error') evtSource.close()
      } catch (_) {}
    }
    evtSource.onerror = () => evtSource.close()

    const formData = new FormData()
    formData.append('file', file)
    formData.append('initials', initials)
    formData.append('hourly_rate', hourlyRate)
    formData.append('headless', headless ? 'true' : 'false')
    formData.append('session_id', sessionId)

    try {
      const response = await fetch('/api/generate', { method: 'POST', body: formData })

      if (!response.ok) {
        const errData = await response.json()
        throw new Error(errData.error || 'Failed to generate timesheets')
      }

      const blob = await response.blob()
      if (blob.size < 100) throw new Error('ZIP appears empty. Check server logs.')
      setZipBlob(blob)
      setSuccess(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
      evtSource.close()
    }
  }

  const handleDownload = () => {
    if (!zipBlob) return
    const url = window.URL.createObjectURL(zipBlob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'timesheets.zip'
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.URL.revokeObjectURL(url)
  }

  const handlePrint = async () => {
    try {
      const response = await fetch('/api/print', { method: 'POST' })
      const data = await response.json()
      if (!response.ok) {
        alert(data.error + '\n\nPlease download the ZIP and print the PDFs manually.')
      } else {
        alert('✅ ' + data.message)
      }
    } catch (err) {
      alert('Error printing: ' + err.message)
    }
  }

  const progressPct = totalSteps > 0 ? Math.round((currentStep / totalSteps) * 100) : 0

  return (
    <div className="glass-panel">
      <h1>MSBM Timesheet Printer</h1>
      <p className="subtitle">Automated bi-weekly calculator and PDF generator</p>

      {/* ── win32print banner ─────────────────────────────────────────── */}
      <div className={`win32-banner ${win32Available ? 'win32-ok' : 'win32-off'}`}>
        <div className="win32-badge">
          {win32Available === null ? '⏳' : win32Available ? '🖨️' : '⚠️'}
        </div>
        <div className="win32-body">
          <strong>
            {win32Available === null
              ? 'Checking Windows print support…'
              : win32Available
              ? 'Windows Direct Printing — Active'
              : 'Windows Direct Printing — Not Installed'}
          </strong>
          <p>
            {win32Available
              ? 'pywin32 is installed. The "Print All" button will send PDFs directly to your default Windows printer without opening any dialog.'
              : 'Install pywin32 to enable one-click direct printing to your Windows printer. Without it, you must download the ZIP and print each PDF manually.'}
          </p>
          {win32Message && (
            <p className={`win32-msg ${win32Message.type === 'error' ? 'win32-msg-error' : 'win32-msg-success'}`}>
              {win32Message.text}
            </p>
          )}
        </div>
        {!win32Available && win32Available !== null && (
          <button
            id="btn-install-win32"
            className="win32-install-btn"
            onClick={handleInstallWin32}
            disabled={win32Installing}
          >
            {win32Installing ? 'Installing…' : 'Enable Windows Printing'}
          </button>
        )}
      </div>
      {/* ────────────────────────────────────────────────────────────────── */}

      {error && <div className="error-banner">{error}</div>}

      {!loading && !success && (
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Excel Timesheet File</label>
            <input
              type="file"
              accept=".xlsx, .xls"
              onChange={(e) => setFile(e.target.files[0])}
              required
            />
          </div>

          <div className="form-group">
            <label>Employee Initials</label>
            <input
              type="text"
              placeholder="e.g. JT"
              value={initials}
              onChange={(e) => setInitials(e.target.value)}
              required
            />
          </div>

          <div className="form-group">
            <label>Hourly Rate</label>
            <input
              type="number"
              value={hourlyRate}
              onChange={(e) => setHourlyRate(e.target.value)}
              required
            />
          </div>

          <div className="checkbox-group">
            <input
              type="checkbox"
              id="headless"
              checked={headless}
              onChange={(e) => setHeadless(e.target.checked)}
            />
            <label htmlFor="headless" style={{ marginBottom: 0 }}>
              Run Hidden (Headless Mode)
            </label>
          </div>

          <button type="submit" id="btn-generate">Generate Timesheets</button>
        </form>
      )}

      {loading && (
        <div className="loader-container">
          <div className="spinner"></div>
          <h3 style={{ color: 'var(--inner-blue)', marginBottom: '0.5rem' }}>Processing Timesheets…</h3>

          {totalSteps > 0 && (
            <div className="progress-bar-container">
              <div className="progress-bar-track">
                <div className="progress-bar-fill" style={{ width: `${progressPct}%` }} />
              </div>
              <span className="progress-bar-label">{currentStep} / {totalSteps} PDFs ({progressPct}%)</span>
            </div>
          )}

          <div className="progress-log">
            {progressMessages.map((msg, idx) => (
              <div
                key={idx}
                className={`progress-line ${
                  msg.includes('ERROR') || msg.includes('FAILED')
                    ? 'progress-error'
                    : msg.includes('OK') || msg.includes('saved')
                    ? 'progress-success'
                    : msg.includes('WARNING')
                    ? 'progress-warn'
                    : ''
                }`}
              >
                <span className="progress-timestamp">{new Date().toLocaleTimeString()}</span>
                {msg}
              </div>
            ))}
            <div ref={progressEndRef} />
          </div>

          {!headless && (
            <p style={{ fontSize: '0.9em', color: 'var(--msbm-red)', marginTop: '1rem' }}>
              A browser window should appear showing the bot's progress.
            </p>
          )}
        </div>
      )}

      {success && (
        <div className="success-container">
          <h2 style={{ color: 'var(--light-blue)' }}>Success! 🎉</h2>
          <p>Your timesheets have been generated.</p>

          {progressMessages.length > 0 && (
            <details className="progress-details">
              <summary>View processing log ({progressMessages.length} events)</summary>
              <div className="progress-log" style={{ maxHeight: '200px' }}>
                {progressMessages.map((msg, idx) => (
                  <div
                    key={idx}
                    className={`progress-line ${
                      msg.includes('ERROR') || msg.includes('FAILED') ? 'progress-error' : msg.includes('OK') || msg.includes('saved') ? 'progress-success' : ''
                    }`}
                  >
                    {msg}
                  </div>
                ))}
              </div>
            </details>
          )}

          <button id="btn-download" onClick={handleDownload}>⬇ Download ZIP Archive</button>
          <button id="btn-print" className="btn-secondary" onClick={handlePrint} disabled={!win32Available}>
            {win32Available ? '🖨️ Print All (Windows Printer)' : '🖨️ Print All (install pywin32 first)'}
          </button>
          <button
            style={{ background: 'transparent', border: '1px solid #ccc', color: '#666', marginTop: '0.5rem' }}
            onClick={() => { setSuccess(false); setProgressMessages([]) }}
          >
            ↩ Start Over
          </button>
        </div>
      )}
    </div>
  )
}

export default App

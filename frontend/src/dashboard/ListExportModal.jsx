export default function ListExportModal({
  isOpen,
  title,
  recordCount,
  isDownloading,
  onClose,
  onDownload,
}) {
  if (!isOpen) return null

  function handleBackdrop(event) {
    if (event.target === event.currentTarget && !isDownloading) onClose()
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={handleBackdrop}>
      <section className="career-modal list-export-modal" role="dialog" aria-modal="true" aria-labelledby="list-export-title">
        <header className="career-modal-header">
          <div>
            <span className="eyebrow">DESCARGAR LISTADO</span>
            <h3 id="list-export-title">{title}</h3>
            <p>{recordCount} registro(s) disponibles para exportar.</p>
          </div>
          <button type="button" className="ghost-button compact-button" onClick={onClose} disabled={isDownloading}>
            Cerrar
          </button>
        </header>

        <div className="career-modal-body list-export-modal-body">
          <p className="list-export-help">Selecciona el formato que necesitas. Ambos documentos incluyen nombres, correos y teléfonos registrados.</p>
          <div className="list-export-options">
            <button type="button" className="list-export-option" onClick={() => onDownload('xls')} disabled={isDownloading}>
              <span className="list-export-format">XLS</span>
              <strong>Descargar para Excel</strong>
              <small>Hoja organizada y editable.</small>
            </button>
            <button type="button" className="list-export-option" onClick={() => onDownload('pdf')} disabled={isDownloading}>
              <span className="list-export-format">PDF</span>
              <strong>Descargar documento PDF</strong>
              <small>Documento paginado para lectura o impresión.</small>
            </button>
          </div>
          {isDownloading ? <p className="status-message success">Generando el documento seleccionado...</p> : null}
        </div>
      </section>
    </div>
  )
}

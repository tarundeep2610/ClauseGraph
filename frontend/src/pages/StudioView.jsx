import { useEffect } from 'react';
import { useLocation, useNavigate, useOutletContext, useParams } from 'react-router-dom';
import ViewerPanel from '../components/ViewerPanel';

// StudioView ("/studio/:docId" and "/studio/:docId/review") is the
// viewer-only page: just the document viewer (PDF + OCR text + Structure
// tree), reusing ViewerPanel exactly as Dashboard.jsx wired it before this
// split.
export default function StudioView() {
  const { docId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const ctx = useOutletContext();
  const { setActiveJobId, setSidebarCollapsed } = ctx;
  const initialReviewOpen = location.pathname.endsWith('/review');

  // Syncs AppShell's activeJobId from the URL on mount and whenever the
  // user navigates to a different document
  useEffect(() => {
    setActiveJobId(docId);
  }, [docId, setActiveJobId]);

  return (
    <div className="studio-view">
      <ViewerPanel
        studioMode={true}
        initialReviewOpen={initialReviewOpen}
        onBackToLibrary={() => navigate('/')}
        viewMode={ctx.viewMode}
        setViewMode={ctx.setViewMode}
        pdfSrc={ctx.pdfSrc}
        pdfStatusLabel={ctx.pdfStatusLabel}
        jobDetails={ctx.jobDetails}
        hasSelection={ctx.hasSelection}
        fileLoading={ctx.fileLoading}
        onPause={() => docId && ctx.handlePause(docId)}
        onResume={() => docId && ctx.handleRetry(docId)}
        onDownloadPdf={() => docId && ctx.handleDownloadFile(docId)}
        onSavePageText={ctx.handleSavePageText}
        onExport={(opts) => docId && ctx.handleExportDocument(docId, opts)}
        sidebarCollapsed={ctx.sidebarCollapsed}
        onToggleSidebar={() => setSidebarCollapsed((v) => !v)}
        onRetryPage={ctx.handleRetryPage}
        retryingPage={ctx.retryingPage}
        onRetryPages={ctx.handleRetryPages}
        reviewPages={ctx.reviewPages}
        onConfirmCorrectPage={ctx.handleConfirmCorrectPage}
        onReindexReferences={(textSource) => docId && ctx.handleReindexReferences(docId, textSource)}
        reindexing={ctx.reindexing}
        referenceStore={ctx.referenceStore}
        scopeRanges={ctx.scopeRanges}
        onCurrentPageChange={ctx.setCurrentPdfPage}
        structureContent={ctx.structureContent}
        onRequestStructureContent={ctx.handleRequestStructureContent}
        parentOverrides={ctx.parentOverrides}
        sortOverrides={ctx.sortOverrides}
        onAddStructureItem={ctx.handleAddStructureItem}
        onEditStructureItem={ctx.handleEditStructureItem}
        onDeleteStructureItem={ctx.handleDeleteStructureItem}
        onMoveStructureItem={ctx.handleMoveStructureItem}
        onOpenSettings={ctx.onOpenSettings}
      />
    </div>
  );
}

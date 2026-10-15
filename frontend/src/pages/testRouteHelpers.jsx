import { render } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import AppShell from './AppShell';
import LibraryHome from './LibraryHome';
import StudioView from './StudioView';

// Shared by AppShell.test.jsx and LibraryHome.submit.test.jsx (migrated from
// the old monolithic Dashboard.test.jsx / Dashboard.submit.test.jsx) — mounts
// the real nested route tree (AppShell as the layout route, LibraryHome at
// its index, StudioView at /studio/:docId[/review]) so tests exercise
// Library/Studio exactly as App.jsx wires them, just without the
// auth/login/callback routes that aren't relevant to these tests.
export function renderApp(initialPath = '/') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/" element={<AppShell />}>
          <Route index element={<LibraryHome />} />
          <Route path="studio/:docId" element={<StudioView />} />
          <Route path="studio/:docId/review" element={<StudioView />} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
}

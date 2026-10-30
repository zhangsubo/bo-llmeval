import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { App as AntApp, ConfigProvider, Spin } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import MainLayout from './layouts/MainLayout';

const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const SuppliersPage = lazy(() => import('./pages/SuppliersPage'));
const ConnectionsPage = lazy(() => import('./pages/ConnectionsPage'));
const ModelsPage = lazy(() => import('./pages/ModelsPage'));
const TasksPage = lazy(() => import('./pages/TasksPage'));
const ResourcesPage = lazy(() => import('./pages/ResourcesPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const ComparisonGroupsPage = lazy(() => import('./pages/ComparisonGroupsPage'));
const MonitoringPage = lazy(() => import('./pages/MonitoringPage'));
const ComparisonPage = lazy(() => import('./pages/ComparisonPage'));
const ReportsPage = lazy(() => import('./pages/ReportsPage'));

export default function App() {
  return (
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#1677ff' } }}>
      <AntApp>
        <BrowserRouter>
          <Suspense fallback={<div style={{ display: 'grid', minHeight: 240, placeItems: 'center' }}><Spin /></div>}>
            <Routes>
              <Route path="/" element={<MainLayout />}>
                <Route index element={<DashboardPage />} />
                <Route path="suppliers" element={<SuppliersPage />} />
                <Route path="connections" element={<ConnectionsPage />} />
                <Route path="models" element={<ModelsPage />} />
                <Route path="comparison-groups" element={<ComparisonGroupsPage />} />
                <Route path="tokenizers" element={<ResourcesPage />} />
                <Route path="datasets" element={<ResourcesPage />} />
                <Route path="templates" element={<ResourcesPage />} />
                <Route path="tasks" element={<TasksPage />} />
                <Route path="monitoring" element={<MonitoringPage />} />
                <Route path="comparisons" element={<ComparisonPage />} />
                <Route path="reports" element={<ReportsPage />} />
                <Route path="settings" element={<SettingsPage />} />
              </Route>
            </Routes>
          </Suspense>
        </BrowserRouter>
      </AntApp>
    </ConfigProvider>
  );
}

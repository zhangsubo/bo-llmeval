import React, { useEffect, useState } from 'react';
import { Button, ConfigProvider, Layout, Menu, Typography, type MenuProps } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import {
  ExperimentOutlined,
  FileSearchOutlined,
  RobotOutlined,
  SettingOutlined,
  PictureOutlined,
  UserOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
} from '@ant-design/icons';
import { type AppConfig, fetchConfig } from './api/client';
import { WorkbenchPage, type WorkbenchPreset } from './pages/WorkbenchPage';
import { ResultsPage } from './pages/ResultsPage';
import { ConfigPage } from './pages/ConfigPage';
import { AssetsPage } from './pages/AssetsPage';

import './App.css';

const { Sider, Content } = Layout;
const { Text } = Typography;

type NavKey = 'workbench' | 'results' | 'config' | 'assets-aigc' | 'assets-real';

const pageLabels: Record<NavKey, string> = {
  workbench: '测试与诊断 / 测试工作台', results: '结果与 AI 解读',
  'assets-aigc': '素材库 / 虚拟人像（AIGC）', 'assets-real': '素材库 / 真人人像', config: '系统设置 / 配置管理',
};
// 新入口统一在此分组注册，避免导航随功能增长挤占内容区。
const navigationItems: MenuProps['items'] = [
  { key: 'diagnostics', type: 'group', label: '测试与诊断', children: [
    { key: 'workbench', icon: <ExperimentOutlined />, label: '测试工作台' },
  ] },
  { key: 'assets', type: 'group', label: '素材库', children: [
    { key: 'assets-aigc', icon: <PictureOutlined />, label: '虚拟人像（AIGC）' },
    { key: 'assets-real', icon: <UserOutlined />, label: '真人人像' },
  ] },
  { key: 'results', icon: <FileSearchOutlined />, label: '结果与 AI 解读' },
  { key: 'settings', type: 'group', label: '系统设置', children: [
    { key: 'config', icon: <SettingOutlined />, label: '配置管理' },
  ] },
];

export const App: React.FC = () => {
  const [collapsed, setCollapsed] = useState(false);
  const [activeTab, setActiveTab] = useState<NavKey>('results');
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [selectedResult, setSelectedResult] = useState<{
    category: string;
    folder: string;
  } | null>(null);
  const [workbenchPreset, setWorkbenchPreset] = useState<WorkbenchPreset | null>(null);
  const [openAnalyzerModal, setOpenAnalyzerModal] = useState(false);

  const loadConfig = async () => {
    try {
      const data = await fetchConfig();
      setConfig(data);
    } catch {
      // ignore initial error
    }
  };

  useEffect(() => {
    loadConfig();
  }, []);

  const handleNavigateToResult = (category: string, folder: string) => {
    setSelectedResult({ category, folder });
    setActiveTab('results');
  };

  const handleJumpToWorkbench = (preset: WorkbenchPreset) => {
    setWorkbenchPreset(preset);
    setActiveTab('workbench');
  };

  return (
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#1677ff', borderRadius: 8 } }}>
      <Layout className="app-shell">
        <Sider className="app-sidebar" theme="light" width={248} collapsedWidth={64}
          collapsible collapsed={collapsed} onCollapse={setCollapsed} breakpoint="lg" trigger={null}>
          <div className="app-brand">
            <div className="app-brand-mark" aria-hidden="true">B</div>
            {!collapsed && <div><strong>接口诊断工作台</strong><span>BO · LLMEVAL</span></div>}
          </div>
          <nav aria-label="主导航" className="app-navigation">
            <Menu mode="inline" selectedKeys={[activeTab]} onClick={(e) => setActiveTab(e.key as NavKey)} items={navigationItems} />
          </nav>
          <div className="app-sidebar-footer">
            {!collapsed && <>
              <Button type="text" icon={<RobotOutlined />} className="app-analyzer-link" onClick={() => {
                setActiveTab('config'); setOpenAnalyzerModal(true);
              }}>AI 解读配置</Button>
              <Text type="secondary" className="app-sidebar-status">
                {config?.analyzer?.configured ? config.analyzer.model : '当前使用内置诊断规则'}
              </Text>
              <Text type="secondary" className="app-sidebar-status">已配凭证 {config?.profiles?.length ?? 0} 组</Text>
            </>}
            <Button type="text" block icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              aria-label={collapsed ? '展开导航' : '收起导航'} onClick={() => setCollapsed(!collapsed)}>
              {!collapsed && '收起导航'}
            </Button>
          </div>
        </Sider>
        <Layout className="app-main">
          <Content className="app-content">
            <div className="app-page-context">{pageLabels[activeTab]}</div>
            {activeTab === 'assets-aigc' && <AssetsPage config={config} key="AIGC" groupType="AIGC" onNavigateToResult={handleNavigateToResult} />}
            {activeTab === 'assets-real' && <AssetsPage config={config} key="LivenessFace" groupType="LivenessFace" onNavigateToResult={handleNavigateToResult} />}
            {activeTab === 'workbench' && (
              <WorkbenchPage config={config} preset={workbenchPreset} onClearPreset={() => setWorkbenchPreset(null)} onNavigateToResult={handleNavigateToResult} />
            )}
            {activeTab === 'results' && (
              <ResultsPage selectedTarget={selectedResult} onSelectTarget={setSelectedResult} onJumpToWorkbench={handleJumpToWorkbench} />
            )}
            {activeTab === 'config' && (
              <ConfigPage defaultOpenAnalyzerModal={openAnalyzerModal} onAnalyzerModalClosed={() => setOpenAnalyzerModal(false)} onConfigSaved={setConfig} />
            )}
          </Content>
        </Layout>
      </Layout>
    </ConfigProvider>
  );
};

export default App;

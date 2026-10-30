import { useState } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, theme } from 'antd';
import {
  DashboardOutlined,
  CloudServerOutlined,
  ApiOutlined,
  AppstoreOutlined,
  GroupOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  ExperimentOutlined,
  PlayCircleOutlined,
  MonitorOutlined,
  SettingOutlined,
  BarChartOutlined,
  FileSearchOutlined,
} from '@ant-design/icons';

const { Header, Sider, Content } = Layout;

const menuItems = [
  { key: '/', icon: <DashboardOutlined />, label: '仪表盘' },
  { key: '/suppliers', icon: <CloudServerOutlined />, label: '供应商管理' },
  { key: '/connections', icon: <ApiOutlined />, label: '连接管理' },
  { key: '/models', icon: <AppstoreOutlined />, label: '模型部署' },
  { key: '/comparison-groups', icon: <GroupOutlined />, label: '对比组' },
  { key: '/tokenizers', icon: <DatabaseOutlined />, label: 'Tokenizer' },
  { key: '/datasets', icon: <FileTextOutlined />, label: '数据集' },
  { key: '/templates', icon: <ExperimentOutlined />, label: '测试模板' },
  { key: '/tasks', icon: <PlayCircleOutlined />, label: '评测任务' },
  { key: '/monitoring', icon: <MonitorOutlined />, label: '监控' },
  { key: '/comparisons', icon: <BarChartOutlined />, label: '结果对比' },
  { key: '/reports', icon: <FileSearchOutlined />, label: '报告版本' },
  { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
];

export default function MainLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { token: { colorBgContainer, borderRadiusLG } } = theme.useToken();

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed} theme="dark">
        <div style={{ height: 32, margin: 16, textAlign: 'center', color: '#fff', fontWeight: 'bold', fontSize: collapsed ? 14 : 16 }}>
          {collapsed ? 'EP' : 'EvalPlat'}
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header style={{ padding: '0 24px', background: colorBgContainer, display: 'flex', alignItems: 'center' }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>LLM API 服务评测管理平台</h2>
        </Header>
        <Content style={{ margin: 24 }}>
          <div style={{ padding: 24, background: colorBgContainer, borderRadius: borderRadiusLG, minHeight: 360 }}>
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  );
}

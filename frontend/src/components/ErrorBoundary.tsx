import React from "react";

interface State {
  error: Error | null;
  info: React.ErrorInfo | null;
}

export class ErrorBoundary extends React.Component<{ children: React.ReactNode }, State> {
  state: State = { error: null, info: null };

  static getDerivedStateFromError(error: Error): State {
    return { error, info: null };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    this.setState({ error, info });
    console.error("[ErrorBoundary] 渲染错误:", error, info);
  }

  handleReload = () => {
    window.location.reload();
  };

  handleReset = () => {
    this.setState({ error: null, info: null });
  };

  render() {
    if (this.state.error) {
      return (
        <div className="h-screen flex items-center justify-center apple-bg p-8">
          <div className="apple-glass-strong max-w-2xl w-full p-6 space-y-4">
            <div className="flex items-center gap-2.5">
              <div className="w-10 h-10 rounded-xl bg-red-100 text-red-600 flex items-center justify-center text-xl font-bold">
                !
              </div>
              <div>
                <h1 className="text-lg font-bold text-phc-ink">页面渲染出错</h1>
                <p className="text-xs text-zinc-500">前端组件异常,已捕获避免白屏</p>
              </div>
            </div>
            <pre className="text-[11px] font-mono bg-red-50/50 text-red-900 p-3 rounded-lg overflow-auto max-h-64 whitespace-pre-wrap break-all border border-red-100">
              {this.state.error.name}: {this.state.error.message}
              {this.state.info?.componentStack && "\n\nStack:\n" + this.state.info.componentStack}
            </pre>
            <div className="flex gap-2 text-xs">
              <button onClick={this.handleReload} className="apple-btn apple-btn-primary">
                重新加载 (Ctrl+Shift+R)
              </button>
              <button onClick={this.handleReset} className="apple-btn apple-btn-secondary">
                重试
              </button>
              <a
                href="https://github.com/lixvanran/ZhangXuefeng-Agent/issues"
                target="_blank"
                rel="noreferrer"
                className="apple-btn apple-btn-secondary ml-auto"
              >
                报告问题
              </a>
            </div>
            <p className="text-[11px] text-zinc-500 leading-relaxed">
              提示:如果是首次打开,先按 <kbd className="px-1.5 py-0.5 rounded bg-black/5 font-mono">Ctrl + Shift + R</kbd> 强制刷新浏览器清缓存。
            </p>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

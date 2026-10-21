import { unstableSetRender } from "antd";
import { createRoot } from "react-dom/client";
import type { Root } from "react-dom/client";

// Ant Design 5's imperative renderer reads createRoot from react-dom, while
// React 19 exports it from react-dom/client. Use the documented v5 hook; keep
// the locked React/Ant versions and clean up each temporary wave/dialog root.
const roots = new WeakMap<Element | DocumentFragment, Root>();
unstableSetRender((node, container) => {
  let root = roots.get(container);
  if (!root) {
    root = createRoot(container);
    roots.set(container, root);
  }
  root.render(node);
  return async () => {
    // Defer disposal until the component's current React commit has finished.
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
    if (roots.get(container) === root) {
      root.unmount();
      roots.delete(container);
    }
  };
});

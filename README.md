## 🚀 Roadmap & Future Features

We aim to keep `smart-pipeline` lightweight and dependency-free while adding professional orchestration capabilities. Here is the plan for the next versions:

### Phase 1: Developer Experience (DX)
- [ ] **Decorator API**: Add a `@pipe.step` decorator to register functions without calling `.add()` manually.
- [ ] **Rich Logging**: Add execution timers (`⏳ Running step...` / `✅ Finished in 0.04s`) to the console output.

### Phase 2: Performance & Intelligence
- [ ] **Smart Caching (Memoization)**: Implement a disk-based cache using `pickle` and `hashlib`. Steps with identical inputs should skip execution and load from disk instantly.
- [ ] **Parallel Execution**: Use `concurrent.futures` to run independent steps simultaneously (e.g., loading two different datasets at the same time).

### Phase 3: Advanced Control Flow
- [ ] **Conditional Branching**: Allow steps to return a `Skip` signal that prevents downstream dependencies from executing (useful for validation checks).
- [ ] **Parameter Sweeps**: Add a utility to run the pipeline multiple times with different input configurations automatically.

---
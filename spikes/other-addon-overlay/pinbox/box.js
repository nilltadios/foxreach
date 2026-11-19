// Six one-digit boxes. Each moves focus to the next a moment after its digit, as such prompts do
// (after their framework re-renders), so keys sent faster than that land in the wrong box.
const boxes = [...document.querySelectorAll("input")];
boxes[0].focus();
boxes.forEach((b, i) => b.addEventListener("input", () => {
  b.value = b.value.slice(-1);
  setTimeout(() => boxes[i + 1]?.focus(), 120);
  parent.postMessage({ pinbox: boxes.map((x) => x.value || "_").join("") }, "*");
}));

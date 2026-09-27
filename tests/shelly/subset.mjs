// Checks that a script uses only the JavaScript subset the Shelly script engine runs.
//
// Based on the Shelly Script Language Reference: `let`/`var`, functions, `new`,
// `delete`, exceptions, String/Number/Array/Math/Date/JSON, Object.keys. Not
// supported: hoisting, classes, promises/async. ES2015+ syntax that the reference
// does not list (arrow functions, `const`, template literals, destructuring,
// spread, default parameters, for-of, ...) is treated as unsupported. Deeply
// nested anonymous functions can crash the device, so every function must be a
// named top-level declaration and callbacks are passed by name.

import * as acorn from "acorn";
import * as walk from "acorn-walk";

// Every AST node type a script may contain (acorn/ESTree names).
const ALLOWED_NODES = new Set([
  "Program",
  "ExpressionStatement",
  "BlockStatement",
  "EmptyStatement",
  "IfStatement",
  "ForStatement",
  "WhileStatement",
  "DoWhileStatement",
  "BreakStatement",
  "ContinueStatement",
  "ReturnStatement",
  "ThrowStatement",
  "TryStatement",
  "CatchClause",
  "SwitchStatement",
  "SwitchCase",
  "VariableDeclaration",
  "VariableDeclarator",
  "FunctionDeclaration",
  "Identifier",
  "Literal",
  "ArrayExpression",
  "ObjectExpression",
  "Property",
  "MemberExpression",
  "CallExpression",
  "NewExpression",
  "UnaryExpression",
  "UpdateExpression",
  "BinaryExpression",
  "LogicalExpression",
  "AssignmentExpression",
  "ConditionalExpression",
  "SequenceExpression",
]);

// Globals that are not available in the Shelly engine (or not wanted).
const FORBIDDEN_GLOBALS = new Set([
  "Promise",
  "Symbol",
  "Map",
  "Set",
  "WeakMap",
  "WeakSet",
  "Proxy",
  "Reflect",
  "setTimeout",
  "setInterval",
  "clearTimeout",
  "clearInterval",
  "require",
  "module",
  "exports",
  "process",
  "globalThis",
  "window",
  "eval",
]);

// Newer built-in methods we avoid: not guaranteed by the reference.
const FORBIDDEN_METHODS = new Set([
  "includes",
  "find",
  "findIndex",
  "startsWith",
  "endsWith",
  "padStart",
  "padEnd",
  "repeat",
  "assign",
  "entries",
  "values",
  "fill",
  "from",
  "of",
  "flat",
  "flatMap",
  "at",
  "trimStart",
  "trimEnd",
]);

/** Return a list of problems ("line N: ...") for `source`; empty means OK. */
export function checkSubset(source) {
  const problems = [];
  let ast;
  try {
    ast = acorn.parse(source, { ecmaVersion: 2015, sourceType: "script", locations: true });
  } catch (err) {
    return [`does not parse as ES2015 script: ${err.message}`];
  }
  const at = (node, text) => problems.push(`line ${node.loc.start.line}: ${text}`);

  walk.full(ast, (node) => {
    if (!ALLOWED_NODES.has(node.type)) {
      at(node, `${node.type} is not supported`);
      return;
    }
    switch (node.type) {
      case "VariableDeclaration":
        if (node.kind === "const") {
          at(node, "const is not supported; use let");
        }
        for (const decl of node.declarations) {
          if (decl.id.type !== "Identifier") {
            at(node, "destructuring is not supported");
          }
        }
        break;
      case "FunctionDeclaration":
        for (const param of node.params) {
          if (param.type !== "Identifier") {
            at(node, "default, rest or destructured parameters are not supported");
          }
        }
        if (node.generator) {
          at(node, "generators are not supported");
        }
        break;
      case "Property":
        if (node.kind !== "init" || node.method || node.shorthand || node.computed) {
          at(node, "only plain `key: value` object properties are supported");
        }
        break;
      case "Literal":
        if (node.regex !== undefined) {
          at(node, "regular expression literals are not supported");
        }
        if (typeof node.value === "string" && /\\u/.test(node.raw)) {
          at(node, "\\u escapes are not supported; use UTF-8 text or \\xHH");
        }
        break;
      case "Identifier":
        if (FORBIDDEN_GLOBALS.has(node.name)) {
          at(node, `${node.name} is not available`);
        }
        break;
      case "MemberExpression":
        if (!node.computed && FORBIDDEN_METHODS.has(node.property.name)) {
          at(node, `.${node.property.name} is not guaranteed on the device`);
        }
        break;
      default:
        break;
    }
  });

  // Functions only at the top level (no nested or anonymous functions).
  walk.ancestor(ast, {
    FunctionDeclaration(node, _state, ancestors) {
      if (ancestors.length !== 2) {
        at(node, `function ${node.id.name} must be declared at the top level`);
      }
    },
  });

  // No hoisting: top-level code may only call functions declared above it.
  const declared = new Set();
  const topLevel = new Set(
    ast.body.filter((n) => n.type === "FunctionDeclaration").map((n) => n.id.name),
  );
  for (const statement of ast.body) {
    if (statement.type === "FunctionDeclaration") {
      declared.add(statement.id.name);
      continue;
    }
    walk.simple(statement, {
      Identifier(node) {
        if (topLevel.has(node.name) && !declared.has(node.name)) {
          at(node, `${node.name} is used before its declaration (no hoisting)`);
        }
      },
    });
  }
  return problems;
}

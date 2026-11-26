package autodev.javachecks;

import com.puppycrawl.tools.checkstyle.api.AbstractCheck;
import com.puppycrawl.tools.checkstyle.api.DetailAST;
import com.puppycrawl.tools.checkstyle.api.TokenTypes;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.Set;
import java.util.regex.Pattern;

/** Check the combined nesting of conditions and loops within a method. */
public final class BusinessCodeCheck extends AbstractCheck {
    private static final int MAX_DEPTH = 3;
    private static final Set<String> LOG_METHODS = Set.of("trace", "debug", "info", "warn", "error");
    private static final Set<String> SQL_METHODS = Set.of("prepareStatement", "executeQuery", "executeUpdate", "execute");
    private static final Pattern LOGGER = Pattern.compile("(?i)(log|logger)");
    private static final Pattern SECRET_ARGUMENT = Pattern.compile(
        "(?i)(password|passwd|secret|apiKey|accessToken|refreshToken|privateKey|cvv|cardNo|idCard|mobilePhone)"
    );
    private static final Pattern SQL_PREFIX = Pattern.compile("(?i)^\"\\s*(select|insert|update|delete)\\b");
    private final Deque<Integer> methodDepths = new ArrayDeque<>();
    private int depth;

    @Override
    public int[] getDefaultTokens() {
        return new int[] {
            TokenTypes.METHOD_DEF, TokenTypes.CTOR_DEF, TokenTypes.LITERAL_IF,
            TokenTypes.LITERAL_SWITCH, TokenTypes.LITERAL_FOR, TokenTypes.LITERAL_WHILE,
            TokenTypes.LITERAL_DO, TokenTypes.METHOD_CALL,
        };
    }

    @Override
    public int[] getAcceptableTokens() {
        return getDefaultTokens();
    }

    @Override
    public int[] getRequiredTokens() {
        return getDefaultTokens();
    }

    @Override
    public void beginTree(DetailAST root) {
        methodDepths.clear();
        depth = 0;
    }

    @Override
    public void visitToken(DetailAST ast) {
        if (isMethod(ast)) {
            methodDepths.push(depth);
            depth = 0;
        } else if (ast.getType() == TokenTypes.METHOD_CALL) {
            checkCall(ast);
        } else if (isNestedBlock(ast) && ++depth > MAX_DEPTH) {
            log(ast, "control.nesting", MAX_DEPTH);
        }
    }

    @Override
    public void leaveToken(DetailAST ast) {
        if (isMethod(ast)) {
            depth = methodDepths.pop();
        } else if (isNestedBlock(ast)) {
            depth--;
        }
    }

    private static boolean isMethod(DetailAST ast) {
        return ast.getType() == TokenTypes.METHOD_DEF || ast.getType() == TokenTypes.CTOR_DEF;
    }

    private static boolean isNestedBlock(DetailAST ast) {
        return ast.getType() != TokenTypes.METHOD_CALL
            && (ast.getType() != TokenTypes.LITERAL_IF || ast.getParent().getType() != TokenTypes.LITERAL_ELSE);
    }

    private void checkCall(DetailAST call) {
        DetailAST expression = call.getFirstChild();
        boolean qualified = expression.getType() == TokenTypes.DOT;
        String method = leafName(expression);
        if ("printStackTrace".equals(method)) {
            log(call, "printStackTrace.forbidden");
        }
        if (!qualified) {
            return;
        }
        String target = leafName(expression.getFirstChild());
        DetailAST args = call.findFirstToken(TokenTypes.ELIST);
        if (args == null) {
            return;
        }
        for (DetailAST arg = args.getFirstChild(); arg != null; arg = arg.getNextSibling()) {
            if (arg.getType() != TokenTypes.EXPR) {
                continue;
            }
            DetailAST value = arg.getFirstChild();
            if (LOGGER.matcher(target).matches() && LOG_METHODS.contains(method)
                    && value.getType() == TokenTypes.IDENT && SECRET_ARGUMENT.matcher(value.getText()).matches()) {
                log(value, "log.sensitive");
            }
            if ((SQL_METHODS.contains(method) || ("jdbcTemplate".equals(target) && "query".equals(method)))
                    && value.getType() == TokenTypes.PLUS && hasSqlPrefix(value)) {
                log(value, "sql.concat");
            }
        }
    }

    private static String leafName(DetailAST expression) {
        return expression.getType() == TokenTypes.DOT ? expression.getLastChild().getText() : expression.getText();
    }

    private static boolean hasSqlPrefix(DetailAST expression) {
        DetailAST first = expression;
        while (first.getType() == TokenTypes.PLUS) {
            first = first.getFirstChild();
        }
        return first.getType() == TokenTypes.STRING_LITERAL && SQL_PREFIX.matcher(first.getText()).find();
    }
}

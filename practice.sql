SELECT
    u.name                       AS 用户姓名,
    u.city                       AS 所在城市,
    SUM(o.amount)                AS total_amount
FROM users u
JOIN orders o
    ON u.user_id = o.user_id
GROUP BY u.user_id, u.name, u.city
ORDER BY total_amount DESC
LIMIT 5;
require('dotenv').config(); //
const { Client, GatewayIntentBits } = require('discord.js');
const express = require('express');
const app = express();

const client = new Client({ 
    intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.MessageContent
    ] 
});

// 웹사이트가 데이터를 가져갈 수 있도록 허용
app.use((req, res, next) => {
    res.header('Access-Control-Allow-Origin', '*');
    res.header('Access-Control-Allow-Headers', 'Origin, X-Requested-With, Content-Type, Accept');
    next();
});

// 웹사이트로 실제 통계를 보내주는 API 통로
app.get('/api/stats', (req, res) => {
    try {
        const serverCount = client.guilds.cache.size;
        const userCount = client.guilds.cache.reduce((acc, guild) => acc + guild.memberCount, 0);

        res.json({
            servers: serverCount,
            users: userCount,
            commands: 1250000 
        });
    } catch (error) {
        res.status(500).json({ error: '통계 계산 실패' });
    }
});

app.listen(3000, () => {
    console.log('📊 통계 API 서버가 3000번 포트에서 실행 중입니다.');
});

client.once('ready', () => {
    console.log(`✨ 봇 로그인 성공: ${client.user.tag}`);
});

client.login(process.env.DISCORD_TOKEN);
import json
import streamlit as st
from langchain.chat_models import ChatOpenAI
from langchain.schema import (SystemMessage, HumanMessage, AIMessage)


def main():
    # JSONファイルからAPIキーを読み込む
    with open('/Users/kii/work/python_study/chat_gpt/ai_chat/config.json') as config_file:
        config = json.load(config_file)
    llm = ChatOpenAI(api_key=config['OPENAI_API_KEY'], temperature=0)

    st.set_page_config(
        page_title="My ChatGPT",
        page_icon="⚙️"
    )
    st.header("My ChatGPT")

    # チャット履歴の初期化
    if "messages" not in st.session_state:
        st.session_state.messages = [
            SystemMessage(content="何かお役に立てることはありますか？")
        ]

    # ユーザーの入力を監視
    if user_input := st.chat_input("聞きたいことを入力してね！"):
        st.session_state.messages.append(HumanMessage(content=user_input))
        with st.spinner("ChatGPT が考えています ..."):
            response = llm(st.session_state.messages)
        st.session_state.messages.append(AIMessage(content=response.content))

    # チャット履歴の表示
    messages = st.session_state.get('messages', [])
    for message in messages:
        if isinstance(message, AIMessage):
            with st.chat_message('assistant'):
                st.markdown(message.content)
        elif isinstance(message, HumanMessage):
            with st.chat_message('user'):
                st.markdown(message.content)
        else:  # isinstance(message, SystemMessage):
            st.write(f"System message: {message.content}")
    
        # サイドバーのタイトルを設定
    st.sidebar.title("ChatGPT")
    
    # サイドバーにオプションボタンを追加
    model = st.sidebar.selectbox("Choose a model", ["GPT-3.5", "GPT-4"])
    
    # サイドバーにボタンを追加
    clear_button = st.sidebar.button("Clear chat history", key="clear")
    
    # サイドバーにスライダーを追加、temperatureの値を選択可能にする
    # 初期値は0.0、最小値は0.0、最大値は2.0、ステップは0.1
    temperature = st.sidebar.slider("Temperature", 0.0, 2.0, 0.0, 0.1)
    
    st.sidebar.markdown("## Costs")
    st.sidebar.markdown("**Total cost**")
    for i in range(3):
        st.sidebar.markdown(f"- ${i+0.01}")
        
        
        

if __name__ == '__main__':
    main()
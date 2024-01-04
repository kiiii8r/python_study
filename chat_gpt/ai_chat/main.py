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
    
if __name__ == '__main__':
    main()
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import _00_common.const as const
from _00_common.s3 import S3Controler
from _00_common.redshift import RedShiftControler
import _00_common.mapper._02_processed_data.order as order
import _00_common.pre_process._01_raw_data.cust_tran_rec.cust_tran_rec as pre
import _00_common.mapper._01_raw_data.staffstart as staffstart
import _03_output_data._00_common.output_common as output_common
from _00_common.redshift_boto3 import commit

s3 = S3Controler(const.S3_BUCKET_NAME)

# 処理期間設定（3か月分）
target_date = datetime.today().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
start_proc_date = target_date + relativedelta(months=-2)
end_proc_date = target_date + relativedelta(months=1)


def main():
    ''' SKU単位処理 '''
    df_sku, channel_cv_fku = get_sku()

    ''' FKU単位処理 '''
    # 型番単位に集計,SKU単位の経由CVを除去
    df_fku = df_sku.drop(columns=[col for col in df_sku.columns if '経由CV' in col]).groupby(by=['型番コード', '日付'], as_index=False).sum()

    # スタスタデータ
    df_stst = get_stst()

    # zozoエンゲージデータの取得
    df_zozo_engage = get_zozo_engage()

    # 結合
    df_fku = df_fku.merge(channel_cv_fku, how='outer')\
                    .merge(df_stst, how='outer')\
                    .merge(df_zozo_engage, how='outer').fillna(0)

    # 出力処理
    for nm, df in zip(['ec_sku', 'ec_fku'],[df_sku, df_fku]):
        # 3か月分出力処理
        for shift in [0, -1, -2]:
            start_date = target_date + relativedelta(months=shift)
            end_date = start_date + relativedelta(days=-1, months=1) if start_date + relativedelta(days=-1, months=1) <= datetime.today() else datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
            out_df = df[(df['日付'] >= start_date) & (df['日付'] <= end_date)]

            # 出力
            str_ym = start_date.strftime('%Y%m')
            s3.upload_dataframe(out_df, 'data/03_outputData/tableau/tran/item/'+nm, f"{nm}_{str_ym}.csv")
            commit("tableau_in", nm, f"data/03_outputData/tableau/tran/item/{nm}/{nm}_{str_ym}.csv", f"where to_char(order_date, 'YYYYMM') = '{str_ym}'")


def get_data_sku():
    '''
    期間絞込の各種データ取得
    '''
    df_orders = pd.DataFrame()
    df_zozo = pd.DataFrame()
    df_cart = pd.DataFrame()
    df_wish = pd.DataFrame()
    channel_cv = pd.DataFrame()
    channel_cv_fku = pd.DataFrame()
    r_request = pd.DataFrame()
    siire = pd.DataFrame()

    # 当年、前年ループ
    for yshift in [0, -1]:
        
        start_date = start_proc_date + relativedelta(years=yshift)
        end_date = end_proc_date + relativedelta(years=yshift)

        # 原価情報取得
        df_price =  output_common.get_price_master(start_date, end_date)

        # ebisu受注データの取得
        df_orders_tmp = get_ebisu_order(df_price, start_date, end_date)

        # zozoデータの取得
        df_zozo_tmp = get_zozo(df_price, start_date, end_date)

        # カート
        df_cart_tmp = get_c_event(start_date, end_date)

        # お気に入り
        df_wish_tmp = get_w_list(start_date, end_date)

        #チャネル別CVを取得
        channel_cv_tmp, channel_cv_fku_tmp = get_channel_cv(df_orders_tmp, start_date, end_date)

        # 再リクエスト
        r_request_tmp = get_r_request(start_date, end_date)

        # 仕入れ情報
        siire_tmp = get_siire(start_date, end_date)
        
        df_orders = pd.concat([df_orders, df_orders_tmp], axis=0)
        df_zozo = pd.concat([df_zozo, df_zozo_tmp], axis=0)
        df_cart = pd.concat([df_cart, df_cart_tmp], axis=0)
        df_wish = pd.concat([df_wish, df_wish_tmp], axis=0)
        channel_cv = pd.concat([channel_cv, channel_cv_tmp], axis=0)
        channel_cv_fku = pd.concat([channel_cv_fku, channel_cv_fku_tmp], axis=0)
        r_request = pd.concat([r_request, r_request_tmp], axis=0)
        siire = pd.concat([siire, siire_tmp], axis=0)

    return df_orders, df_zozo, df_cart, df_wish, channel_cv, channel_cv_fku, r_request, siire


def get_sku():

    # データ取得
    df_orders, df_zozo, df_cart, df_wish, channel_cv, channel_cv_fku, r_request, siire = get_data_sku()

    # 未キャンセルのebisu受注データの加工
    df_orders, df_ord_4ws = culc_ebisu_order(df_orders)

    # 会員系
    cus_details_num = get_cus_details(df_orders)

    #予約受注の抽出
    df_re_num = get_reserve_order(df_orders)

    #セール受注の抽出
    df_sale_num = get_sale_order(df_orders)

    # 結合
    df_sku = pd.merge(df_ord_4ws, df_sale_num, how='left', on=['SKU', '日付'])\
                .merge(df_re_num, how='left', on=['SKU', '日付'])\
                .merge(cus_details_num, how='left', on=['SKU', '日付'])\
                .merge(df_cart, how='outer', on=['SKU', '日付'])\
                .merge(df_wish, how='outer', on=['SKU', '日付'])\
                .merge(channel_cv, how='outer', on=['SKU', '日付'])\
                .merge(r_request, how='outer', on=['SKU', '日付'])\
                .merge(siire, how='outer', on=['SKU', '日付'])\
                .merge(df_zozo, how='outer', on=['SKU', '日付']).fillna(0)

    # 前年実績を付与
    df_sku_ly = df_sku[['SKU', '日付',  '受注金額', '受注数', '売上金額', '売上数', 'ZOZO_受注金額', 'ZOZO_受注数', 'ZOZO_売上金額', 'ZOZO_売上数']].rename(columns={'受注金額': '前年受注金額', '受注数': '前年受注数', '売上金額': '前年売上金額', '売上数': '前年売上数', 'ZOZO_受注金額': 'ZOZO_前年受注金額', 'ZOZO_受注数': 'ZOZO_前年受注数', 'ZOZO_売上金額': 'ZOZO_前年売上金額', 'ZOZO_売上数': 'ZOZO_前年売上数'}).copy()
    df_sku_ly['日付'] = df_sku_ly['日付'].apply(lambda x: x + relativedelta(years=1))
    df_sku = df_sku.merge(df_sku_ly, how='outer', on=['SKU', '日付']).fillna(0)

    # 型番コードを付与する
    df_sku['型番コード'] = df_sku['SKU'].str[:13]

    return df_sku, channel_cv_fku


def get_ebisu_order(df_price, start_date, end_date):
    '''
    ebisu全受注データの取得
    '''

    rsc = RedShiftControler()
    with open('_00_common/sql/orders/orders_0009.sql', 'r', encoding='utf-8') as f:
        query = f.read()

    df_orders = rsc.get_df(query.format(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'), start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))

    # 前処理
    df_orders['日付'] = df_orders['order_date'].apply(lambda x : x.replace(hour=0, minute=0, second=0))
    df_orders['send_date'] = pd.to_datetime(df_orders['send_date'], format='%Y-%m-%d')

    df_orders = df_orders.rename(columns={'item_code': 'SKU', 'member_waribiki': '会員割引'})
    df_orders['型番コード'] = df_orders['SKU'].str[0:13]

    # 原価情報結合
    df_orders = output_common.merge_wholesale_price(df_orders, df_price, 'ebisu')

    return df_orders


def culc_ebisu_order(df_orders):

    # 各割引額を算出
    order_q = df_orders[['order_no', 'teika']].groupby('order_no', as_index=False).sum().rename(columns={'teika': '受注ごとの総金額'})
    df_orders = df_orders.merge(order_q, how='left')

    df_orders['ポイント割引'] = df_orders['point_waribiki'] / df_orders['受注ごとの総金額'] * df_orders['teika']
    df_orders['クーポン割引'] = df_orders['coupon_waribiki'] / df_orders['受注ごとの総金額'] * df_orders['teika']
    df_orders['まとめ買い割引'] = df_orders['bundle_sale_waribiki'] / df_orders['受注ごとの総金額'] * df_orders['teika']

    # 金額,粗利額を算出
    df_orders['金額'] = (df_orders['teika'] - df_orders['会員割引']) * df_orders['quantity'] - df_orders['まとめ買い割引']
    df_orders['原価合計'] = df_orders['原価'] * df_orders['quantity']
    df_orders['粗利額'] = df_orders['金額'] - df_orders['原価合計']
    
    # 受注数と金額を算出
    df_num = df_orders[['SKU', '日付', 'quantity', '金額', '会員割引', 'ポイント割引', 'クーポン割引', 'まとめ買い割引', '粗利額', '原価合計']].groupby(['SKU', '日付'], as_index=False).sum().rename(columns={'quantity': '受注数', '金額': '受注金額'})

    # 受注数と金額を算出（会員受注数）
    df_num_mem = df_orders[df_orders['kaiin_no'].notnull()]
    df_num_mem = df_num_mem[['SKU', '日付', 'quantity', '金額']].groupby(['SKU', '日付'], as_index=False).sum().rename(columns={'quantity': '会員受注数', '金額': '会員受注金額'})

    # 売上数と金額を算出（発送ベース）
    df_num_sent = df_orders[df_orders['send_date'].notnull()]
    df_num_sent = df_num_sent[['SKU', 'send_date', 'quantity', '金額']].groupby(['SKU', 'send_date'], as_index=False).sum().rename(columns={'send_date':'日付', 'quantity': '売上数', '金額': '売上金額'})

    # 返品数と返品金額を算出
    df_henpin = df_orders[df_orders['quantity'] < 0]
    df_henpin = df_henpin[['SKU', '日付', 'quantity', '金額']].groupby(['SKU', '日付'], as_index=False).sum().rename(columns={'quantity': '返品数', '金額': '返品金額'})
    df_henpin['返品数'] = df_henpin['返品数'] * -1
    df_henpin['返品金額'] = df_henpin['返品金額'] * -1

    # 結合
    df_ord = pd.merge(df_num, df_num_mem, how='left')\
                .merge(df_num_sent, how='outer')\
                .merge(df_henpin, how='outer').fillna(0)

    # k週前受注数
    df_ord_copy = df_ord.copy()
    for i in range(1, 5):
        df_w = df_ord_copy[['日付', 'SKU', '受注数']].rename(columns={'受注数': '受注数（' + str(i) + '週前)'})
        df_w['日付'] = df_w['日付'] + timedelta(days=7 * i)
        df_ord = pd.merge(df_ord, df_w, how='outer', on=['日付', 'SKU'])

    return df_orders, df_ord


def get_zozo_data(kbn, df_price, start_date, end_date):
    sql_file = '_00_common/sql/zozo/order_simple_0001.sql' if kbn == '受注' else '_00_common/sql/zozo/ship_simple_0001.sql'

    rsc = RedShiftControler()
    with open(sql_file, 'r', encoding='utf-8') as f:
        query = f.read()

    df_zozo = rsc.get_df(query.format(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))

    # 前処理
    df_zozo = df_zozo[(df_zozo['item_code'].notnull()) & ~((df_zozo['code'].isnull()) & ((df_zozo['cs_code'].isin(['※入庫禁止', '入荷禁止']))|(df_zozo['cs_code'].isnull())))].rename(columns={'item_code': '型番コード', 'date': '日付'}).reset_index()
    df_zozo['cs_code'] = df_zozo['cs_code'].str.replace('【入荷禁止】', '')
    df_zozo['SKU'] = df_zozo['code'].fillna(df_zozo['型番コード'] + df_zozo['cs_code'])
    df_zozo.loc[~df_zozo['SKU'].str.match('^\d{17}$'), 'SKU'] = df_zozo['型番コード'] + df_zozo['cs_code'].str[:4]

    df_zozo = df_zozo[df_zozo['SKU'].str.len() == 17]

    # if len(df_zozo.loc[~df_zozo['SKU'].str.match('^\d{17}$')]) > 0:
    #     skus = df_zozo.loc[~df_zozo['SKU'].str.match('^\d{17}$')]['SKU'].unique()
    #     raise Exception(f"想定外のSKUが入りました\n{skus}")

    # 粗利の追加
    df_zozo = output_common.merge_wholesale_price(df_zozo, df_price, 'zozo')
    df_zozo['原価合計'] = df_zozo['原価'] * df_zozo['quantity']
    df_zozo['粗利額'] = df_zozo['total_sales'] - df_zozo['原価合計']

    return df_zozo


def get_zozo(df_price, start_date, end_date):
    # zozo受注・売上データの取得
    df_zozo_order = get_zozo_data('受注', df_price, start_date, end_date)
    df_zozo_ship = get_zozo_data('売上', df_price, start_date, end_date)

    df_zozo_or = df_zozo_order[['日付', 'SKU', 'total_sales', 'quantity', '粗利額', '原価合計']].groupby(['日付', 'SKU'], as_index=False).sum()\
                    .rename(columns={'total_sales':'ZOZO_受注金額', 'quantity':'ZOZO_受注数', '原価合計':'ZOZO_受注原価合計', '粗利額':'ZOZO_受注粗利額'})
    df_zozo_or_re = df_zozo_order[df_zozo_order['sale_type'] == '予約'][['日付', 'SKU', 'total_sales', 'quantity']].groupby(['日付', 'SKU'], as_index=False).sum().rename(columns={'total_sales': 'ZOZO_予約金額', 'quantity': 'ZOZO_予約数'})
    df_zozo_sh = df_zozo_ship[['日付', 'SKU', 'total_sales', 'quantity', '粗利額', '原価合計']].groupby(['日付', 'SKU'], as_index=False).sum()\
                    .rename(columns={'total_sales':'ZOZO_売上金額', 'quantity':'ZOZO_売上数', '原価合計':'ZOZO_売上原価合計', '粗利額':'ZOZO_売上粗利額'})
    
    '''結合'''
    df_ord = df_zozo_or.merge(df_zozo_or_re, how='outer')\
                    .merge(df_zozo_sh, how='outer').fillna(0)

    return df_ord


def get_zozo_engage():
    '''
    zozoのエンゲージデータを取得
    '''

    rs = RedShiftControler()
    # zozoお気に入り
    with open('_00_common/sql/zozo/favorite_item_0001.sql', 'r', encoding='utf-8') as f:
        query = f.read()
    df_zozo_fav = rs.get_df(query.format(start_proc_date.strftime('%Y-%m-%d'), end_proc_date.strftime('%Y-%m-%d')))

    # zozoエンゲージデータ
    with open('_00_common/sql/zozo/engage_data_0001.sql', 'r', encoding='utf-8') as f:
        query = f.read()
    df_zozo_engage = rs.get_df(query.format(start_proc_date.strftime('%Y-%m-%d'), end_proc_date.strftime('%Y-%m-%d')))

    return df_zozo_fav.merge(df_zozo_engage, how='outer').rename(columns={'date': '日付', 'fku': '型番コード'}).groupby(['日付', '型番コード'], as_index=False).sum()


# 顧客軸購買
def get_cus_details(df_orders):

    # 新規購入日の付与
    df_1st_date = pre.get_1st_date().rename(columns={'SummaryDateId': 'send_date', 'KaiinNo':'kaiin_no'})
    df_orders2 = df_orders.merge(df_1st_date, how='left')

    # デイトナ新規
    df_orders_dn = df_orders2[df_orders2['初回購入フラグ'] == 1]
    df_orders_dn = df_orders_dn[['SKU', '日付', 'quantity', '金額']].groupby(['SKU', '日付'], as_index=False).sum().rename(columns={'quantity': '新規受注数', '金額': '新規受注金額'})

    # EC新規
    df_orders_en = df_orders2[(df_orders2['EC初回購入フラグ'] == 1) & (df_orders2['初回購入フラグ'] != 1)]
    df_orders_en = df_orders_en[['SKU', '日付', 'quantity', '金額']].groupby(['SKU', '日付'], as_index=False).sum().rename(columns={'quantity': 'EC新規受注数', '金額': 'EC新規受注金額'})

    return df_orders_dn.merge(df_orders_en, how='outer').fillna(0)


# セール受注情報を取得
def get_sale_order(df_orders):

    # 予約受注（概算）を除外する
    df_order_ex_re = df_orders[~df_orders['item_name'].str.contains('【予約商品】')]

    # 会員割引とまとめ買いの受注取得
    df_orders_sa1 = df_order_ex_re[(df_order_ex_re['会員割引'] != 0) | (df_order_ex_re['まとめ買い割引'] != 0)]
    df_orders_sa2 = df_order_ex_re[(df_order_ex_re['会員割引'] == 0) & (df_order_ex_re['まとめ買い割引'] == 0)]

    df_orders_sa2['タイムセール値引き'] = (df_orders_sa2['proper_price'].fillna(df_orders_sa2['teika']) - df_orders_sa2['teika']) * df_orders_sa2['quantity']
    df_orders_sa2 = df_orders_sa2[df_orders_sa2['タイムセール値引き'] > 0]

    target_columns = ['SKU', '日付', 'quantity', '金額']
    df_orders_sa3 = pd.concat([df_orders_sa1[target_columns], df_orders_sa2[target_columns + ['タイムセール値引き']]])
    df_order_ts_num = df_orders_sa3.groupby(['SKU', '日付'], as_index=False).sum().rename(columns={'quantity': 'セール受注数', '金額': 'セール受注金額'})

    return df_order_ts_num


# 予約受注情報を取得
def get_reserve_order(df_orders):

    # 予約情報の取得と加工
    df_re = order.get_item_reserve()
    df_re['予約終了日'] = df_re['予約終了日'].fillna(datetime.strptime('2080010100', '%Y%m%d%H'))
    df_order_re = pd.merge(df_orders, df_re.rename(columns={'sku': 'SKU'}), how='left', on='SKU')

    # 予約受注の抽出と算出
    df_order_re = df_order_re[(df_order_re['order_date'] >= df_order_re['予約開始日']) & (df_order_re['order_date'] <= df_order_re['予約終了日'])]
    df_re_num = df_order_re[['SKU', '日付', 'quantity', '金額']].groupby(['SKU', '日付'], as_index=False).sum().rename(columns={'quantity': '予約受注数', '金額': '予約受注金額'})

    # 予約受注数の概算取得（商品名に予約が入ってたものを抽出）
    df_re_rough = df_orders[df_orders['item_name'].str.contains('【予約商品】')]
    df_re_rough = df_re_rough[['SKU', '日付', 'quantity', '金額']].groupby(['SKU', '日付'], as_index=False).sum().rename(columns={'quantity': '予約受注数(概算)', '金額': '予約受注金額(概算)'})

    df_re_all = df_re_num.merge(df_re_rough, how='outer').fillna(0)

    return df_re_all


#カート追加情報を取得
def get_c_event(start_date, end_date):

    rsc = RedShiftControler()
    with open('_00_common/sql/cart_event/cart_event_0002.sql', 'r', encoding='utf-8') as f:
        query = f.read()

    df_cart = rsc.get_df(query.format(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))

    # 文字含むSKU削除
    df_cart = df_cart[(df_cart['added_item_id'].str.match('^[0-9]{13}-_[0-9]{2}-[0-9]{2}$',na=False)) | (df_cart['added_item_id'].str.match('^[0-9]{13}-_[0-9]{2}-[0-9]{2}$',na=False))] #WEBログのテストゴミデータを除外
    df_cart['added_item_id'] = df_cart['added_item_id'].str.replace('_','').str.replace('-','')
    df_cart['日付'] = df_cart['sync_date_jst'].apply(lambda x : x.replace(hour=0, minute=0, second=0, microsecond=0))

    # カート登録数
    df_cart_sku = df_cart[['日付', 'added_item_id', 'quantity']].groupby(['日付', 'added_item_id'], as_index=False).sum()

    # 基幹システム刷新後、5レコードだけ13桁データが混ざっているので除外
    df_cart_sku = df_cart_sku[df_cart_sku['added_item_id'].str.len() == 17]

    return df_cart_sku.rename(columns={'added_item_id': 'SKU', 'quantity': 'カート登録数'})


def get_w_list(start_date, end_date):
    '''
    お気に入りの取得
    '''
    rsc = RedShiftControler()
    # お気に入りリスト
    with open('_00_common/sql/wish_list/wish_list_0002.sql', 'r', encoding='utf-8') as f:
        query = f.read()
    df_wish = rsc.get_df(query.format(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))

    # 日付
    df_wish['日付'] = df_wish['regist_date'].apply(lambda x : x.replace(hour=0, minute=0, second=0))

    # SKU、型番コード
    df_wish['SKU'] = df_wish['sku'].str.replace('-', '').str.replace('_', '')

    # 文字含むSKU削除
    df_wish = df_wish[(df_wish['sku'].str.match('^[0-9]{13}-_[0-9]{2}-[0-9]{2}$',na=False)) \
        | (df_wish['sku'].str.match('^[0-9]{13}-_[0-9]{2}-[0-9]{2}$',na=False))]

    # お気に入り登録数
    df_wish = df_wish[['member_id', 'SKU', '日付']]
    df_wish_1 = df_wish.groupby(['SKU','日付']).count().reset_index()[['member_id', 'SKU', '日付']].rename(columns={'member_id': 'お気に入り登録数'})

    return df_wish_1


def get_channel_order(start_date, end_date):
    '''
    チャネル別受注データ取得
    '''
    rsc = RedShiftControler()
    with open('_00_common/sql/ga/channel_order_0002.sql', 'r', encoding='utf-8') as f:
        query = f.read()

    output = rsc.get_df(query.format(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
    output.columns = ['日付', '参照元', 'メディア', '受注番号', 'CV']
    output['日付'] = pd.to_datetime(output['日付'], format='%Y-%m-%d')

    return output


def get_channel_cv(df_orders, start_date, end_date):
    '''
    チャネル別経由CV取得
    '''
    rename = {'Facebook':'Facebook経由CV', 'Instagram':'Instagram経由CV', 'LINE':'LINE経由CV', 'Twitter':'Twitter経由CV', 'Youtube':'Youtube経由CV', 'その他':'その他経由CV', 'アプリ':'アプリ経由CV', 
        'ダイレクト':'ダイレクト経由CV', 'メール：MAシナリオ':'メール：MAシナリオ経由CV', 'メール：その他':'メール：その他経由CV', 'メール：メルマガ':'メール：メルマガ経由CV','広告：Affiliate':'広告：Affiliate経由CV', 
        '広告：cpc':'広告：cpc経由CV', '広告：display':'広告：display経由CV', '自然流入':'自然流入経由CV'}

    # GA取得
    df_channel = output_common.get_channel(get_channel_order(start_date, end_date))[['日付', 'チャネル', '受注番号', 'CV']]
    if len(df_channel.index) == 0:
        return pd.DataFrame(), pd.DataFrame()
        
    df_channel = pd.merge(df_orders, df_channel, left_on=['日付', 'order_no'], right_on=['日付', '受注番号'], how='left')

    # 返品・キャンセルの除去
    df_channel = df_channel[(df_channel['チャネル'].notnull()) & (df_channel['quantity'] > 0)]
    # 日付xSKU CV集計
    df_channel_sku = df_channel[['日付', 'SKU', 'チャネル', 'CV']].groupby(['日付', 'SKU', 'チャネル'], as_index=False).sum()

    # ピボット形式に成形
    df_channel_sku = pd.pivot_table(df_channel_sku, index=['日付', 'SKU'], columns='チャネル', values='CV').reset_index().rename(columns=rename)

    # 日付x商品型番　CV集計
    df_channel_fku = df_channel.copy()
    df_channel_fku['型番コード']= df_channel_fku['SKU'].str[:13]

    # 同一取引で色・サイズ違いの商品購入時にCVが2重でカウントされないよう、重複除去
    df_channel_fku = df_channel_fku.drop_duplicates(['日付', '型番コード', 'チャネル', '受注番号'])[['日付', '型番コード', 'チャネル', 'CV']].groupby(['日付', '型番コード', 'チャネル'], as_index=False).sum()
    df_channel_fku = pd.pivot_table(df_channel_fku, index=['日付', '型番コード'], columns='チャネル', values='CV').reset_index().rename(columns=rename)


    return df_channel_sku, df_channel_fku


#スタスタデータの取得と加工
def get_stst():

    dfs_list = [{'content': get_c_content(), 'product': staffstart.get_coordinate_product(), 'master': staffstart.get_coordinate_master()},
        {'content': get_a_content(), 'product': staffstart.get_article_product(), 'master': staffstart.get_article_master()},]

    df_ret = pd.DataFrame()
    for j, dfs in enumerate(dfs_list):
        c_name = 'coordinate' if j == 0 else 'article'
        c_name_jp = 'スナップ' if j == 0 else 'ブログ'

        df1 = dfs['content'].copy()
        df2 = dfs['product'].copy()
        df3 = dfs['master'].copy()

        # 前処理
        df1['d_sku'] = df1['d_sku'].fillna('')
        df1['content_id'] = df1['content_id'].astype(int).astype(str)
        df1_1 = df1[['content_id', 'pv', 'date']]
        df2 = df2[[c_name + '_id', 'base_product_code', 'product_code']]
        df2 = df2.rename(columns={c_name + '_id': 'content_id', 'product_code': 'sku', 'base_product_code': '型番コード'})
        df2['content_id'] = df2['content_id'].astype(str)


        ''' 掲載数、登録数の算出 '''

        # 商品別の記事を集計
        df3_1 = df3[[c_name + '_id', 'published_at']].rename(columns={c_name + '_id': 'content_id', 'published_at': 'date'})
        df2_2 = pd.merge(df2, df3_1, how="left", on=['content_id'])

        #掲載中のスナップに絞る
        df2_2['date'] = df2_2['date'].apply(lambda x : x.replace(hour=0, minute=0, second=0))
        df2_3 = df2_2[df2_2['date'] < datetime.now()]

        # スナップTTL登録数の算出
        pos = df2_3[['型番コード', 'content_id']].groupby(['型番コード'], as_index=False).count().rename(columns={'content_id': c_name_jp + 'TTL登録数'})

        # スナップ日別登録数の算出
        pos2 = df2_3[['型番コード', 'content_id', 'date']].groupby(['型番コード', 'date'], as_index=False).count().rename(columns={'content_id': c_name_jp + '日別登録数'})


        ''' PV, CVの算出 '''

        #PV数の算出
        df2_1 = pd.merge(df2, df1_1, how="left", on=['content_id'])
        pv = df2_1[['型番コード', 'pv', 'date']].groupby(['型番コード', 'date'], as_index=False).sum().rename(columns={'pv': c_name_jp + 'PV数'})

        # スタスタ投稿経由CV数(型番コード×日付ごと)の算出
        df_c = df1[df1['d_sku'] != ''][['content_id', 'd_sku', 'date']].reset_index(drop=True)

        d = df_c.assign(d_sku=df_c['d_sku'].str.split(',')).explode('d_sku').rename(columns={'content_id': 'ID', 'd_sku': '商品'})
        d['sku'] = np.nan
        d[c_name_jp + 'CV数'] = np.nan

        d = d.dropna(how='all').reset_index(drop=True)
        d['商品'] = d['商品'].str.replace('）', '')
        d = d[d['商品']!=''].reset_index(drop=True)
        
        for i in range(len(d)):
            d['sku'][i] = d['商品'][i].split('（')[0]
            d[c_name_jp + 'CV数'][i] = d['商品'][i].split('（')[1]

        # 集計
        d['型番コード'] = d['sku'].astype(int).astype(str).str[0:13]
        d[c_name_jp + 'CV数'] = d[c_name_jp + 'CV数'].astype(int)
        cv = d[['型番コード', c_name_jp + 'CV数', 'date']].groupby(['型番コード', 'date'], as_index=False).sum()


        ''' 結合処理 '''  

        df = pd.merge(pos2, pv, how="outer")
        df = pd.merge(df, cv, how="outer", on=['型番コード', 'date'])
        df = pd.merge(df, pos, how="left")
        df = df.fillna(0)
        df.loc[df['date'] == 0, 'date'] = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

        df = df[df['型番コード'].str.len() == 13]
        if j == 0:
            df_ret = df.copy()
        else:
            df_ret = df_ret.merge(df, how='outer', on=['型番コード', 'date'])
    
    df_ret = df_ret.fillna(0)
    return df_ret.rename(columns={'date': '日付'})[['型番コード', '日付', 'スナップTTL登録数', 'スナップ日別登録数', 'スナップPV数', 'スナップCV数', 'ブログTTL登録数', 'ブログ日別登録数', 'ブログPV数', 'ブログCV数']]


#スタスタcoordinate_contentの取得
def get_c_content():

    rsc = RedShiftControler()
    with open('_00_common/sql/coordinate_content/coordinate_content_0001.sql', 'r', encoding='utf-8') as f:
        query = f.read()

    output = rsc.get_df(query.format(start_proc_date.strftime('%Y-%m-%d'), end_proc_date.strftime('%Y-%m-%d')))
    output['date'] = pd.to_datetime(output['date'], format='%Y-%m-%d')

    return output


def get_a_content():
    '''
    スタスタarticle_contentの取得
    '''
    rsc = RedShiftControler()
    with open('_00_common/sql/article_content/article_content_0001.sql', 'r', encoding='utf-8') as f:
        query = f.read()

    output = rsc.get_df(query.format(start_proc_date.strftime('%Y-%m-%d'), end_proc_date.strftime('%Y-%m-%d')))
    output['date'] = pd.to_datetime(output['date'], format='%Y-%m-%d')

    return output


def get_r_request(start_date, end_date):
    '''
    再リクの取得
    '''
    rsc = RedShiftControler()
    with open('_00_common/sql/restock_request/restock_request_0001.sql', 'r', encoding='utf-8') as f:
        query = f.read()        
    df = rsc.get_df(query.format(start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d')))
    df['日付'] = df['regist_datetime'].apply(lambda x : x.replace(hour=0, minute=0, second=0))
    df['SKU'] = df['sku'].str.replace('-', '').str.replace('_', '')

    return df[['SKU', '日付', 'user_id']].groupby(['SKU', '日付'],as_index=False).count().rename(columns={'user_id': '再リク数'})


def get_siire(start_date, end_date):
    '''
    仕入れ情報の取得
    '''
    rsc = RedShiftControler()
    with open('_00_common/sql/meisai/meisai_0005.sql', 'r', encoding='utf-8') as f:
        query = f.read()
    df = rsc.get_df(query.format(start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d')))

    df['ddate'] = df['ddate'].astype(str)
    df['ddate'] = pd.to_datetime(df['ddate'], format='%Y%m%d', errors='coerce')
    df['su'] = df.apply(lambda row: row['su'] if row['kubun'] == '0022' else -1 * row['su'], axis=1)
    df['amt1'] = df.apply(lambda row: row['amt1'] if row['kubun'] == '0022' else -1 * row['amt1'], axis=1)
    df['amt2'] = df.apply(lambda row: row['amt2'] if row['kubun'] == '0022' else -1 * row['amt2'], axis=1)

    return df[['tanp', 'ddate', 'su', 'amt1', 'amt2']].groupby(['tanp', 'ddate'], as_index=False).sum().rename(columns={'tanp': 'SKU', 'ddate':'日付', 'su': '仕入数', 'amt1': '仕入原価', 'amt2': '仕入売価'})

if __name__ == '__main__':
    main()

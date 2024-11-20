import streamlit as st
import pandas as pd
from plotly import graph_objs as go
from prophet import Prophet
from prophet.plot import plot_plotly, plot_components_plotly

# st.set_page_config(layout="wide")
# Title
st.set_page_config(layout="wide")
st.title('Hotel Chilli Stats')

# Open JSON file
@st.cache_data(ttl='15m')
def load_data():
    df = pd.read_csv('data.json', names=['date', 'count'], on_bad_lines='skip')
    # Set the correct ime for Brazil
    df['datetime'] = pd.to_datetime(df['date'], errors='coerce') - pd.Timedelta(hours=3)
    # # Format date to YYYY-MM-DD HH:MM
    df['date'] = pd.to_datetime(df['datetime'], utc=True).dt.strftime('%Y-%m-%d %H:%M')
    df['day'] = pd.to_datetime(df['datetime'], utc=True).dt.day_name()
    df['hour'] = pd.to_datetime(df['datetime'], utc=True).dt.hour
    return df

df = load_data()

# Add time picker
with st.sidebar:
    start_day = st.date_input('Start', value=df['datetime'].max() - pd.Timedelta(days=7))
    start_hour = st.time_input('Start time', value=df['datetime'].max() - pd.Timedelta(days=7))
    end_time = st.date_input('End time', value=df['datetime'].max())
    end_hour = st.time_input('End time', value=df['datetime'].max())
    n_change_points = st.slider('Number of change points', min_value=0, max_value=50, value=5)
    changepoint_prior_scale = st.slider('Changepoint prior scale', min_value=0.01, max_value=1.0, value=0.05, step=0.01)


c1, c2 = st.columns([1, 1])

df = df[(df['datetime'] >= pd.to_datetime(str(start_day) + ' ' + str(start_hour))) & (df['datetime'] <= pd.to_datetime(str(end_time) + ' ' + str(end_hour)))]
df = df.rename(columns={'date': 'ds', 'count': 'y'})
# Plot line chart using plotly
fig = go.Figure()
fig.add_trace(go.Scatter(x=df['ds'], y=df['y'], mode='lines'))
fig.update_layout(
    title='Hotel Chilli Stats',
    xaxis_title='Time',
    yaxis_title='Count',
)

fig2 = go.Figure()
# Add trace grouping by hour
hourly = (
    df
    .assign(ds=pd.to_datetime(df['datetime']).dt.strftime('%Y-%m-%d %H:00'))
    .loc[:, ['ds', 'y']].groupby('ds', as_index=False).mean()
)
daily = (
    df
    .assign(ds=pd.to_datetime(df['datetime']).dt.strftime('%Y-%m-%d'))
    .loc[:, ['ds', 'y']].groupby('ds', as_index=False).mean()
)
fig2.add_trace(go.Scatter(x=hourly['ds'], y=hourly['y'], mode='lines'))
fig2.update_layout(
    title='Hotel Chilli Stats',
    xaxis_title='Time',
    yaxis_title='Count',
)

with c1:
    st.plotly_chart(fig, use_container_width=True)
    st.plotly_chart(fig2, use_container_width=True)
    # Plot bar chart by day
    st.bar_chart(df.loc[:, ['day', 'y']].groupby('day').mean())
    # Plot bar chart by hour
    st.bar_chart(df.loc[:, ['hour', 'y']].groupby('hour').mean())




from matplotlib import pyplot as plt
from statsmodels.graphics.tsaplots import plot_acf, plot_pacf


with c2:
    # Add the matplotlib chart of the forecast
    fig, ax = plt.subplots()
    pd.plotting.autocorrelation_plot(df.y, ax=ax)
    # for i in range(1, 10):
    #     plt.vlines(i*24*60/15, -0.4, 0.5, colors='r')
    for i in range(20):
        plt.vlines(i*12*60/15, -0.4, 0.5, colors='r')
    #Set the x range to 100
    # ax.set_xlim([50, 100])
    # plot_pacf(df['y'].tolist(), lags=100, ax=ax)
    st.pyplot(fig)